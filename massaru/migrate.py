"""Миграция стора (NEMEQ_HOME_LAYOUT v0.6 §7).

State machine с восстановлением после `kill -9` на любом переходе. Ключевое
требование: повторный запуск либо завершает, либо откатывает — но **никогда**
не публикует частичное дерево.

Состояния (v0.6 §7.2):

    prepared  -> маркер записан и fsynced, staging создан
    copying   -> идёт копирование
    verifying -> копия готова, идёт fsck + digest
    publishing-> intent записан ДО rename
    published -> rename выполнен
    reported  -> отчёт записан, успех
    failed:<reason>

Правило восстановления `publishing` — все шесть комбинаций `var`/staging/манифест.

Коды возврата: см. ExitCode.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from .digest import DigestError, content_digest, metadata_digest, store_digest
from .layout import lock_path


class ExitCode(IntEnum):
    """Коды возврата migrate (v0.6 §7.9)."""

    OK = 0
    BAD_INVOCATION = 2
    PORT_BUSY = 3
    LOCK_BUSY = 4
    IDENTITY_MISMATCH = 5
    DIRTY_WAL = 6
    NO_RULE = 7
    UNSUPPORTED_FS = 8
    SUPPLY_HASH = 9
    DATA_DIGEST = 10
    IO_ERROR = 11
    HEALTH_FAILED = 12
    VERSION_SKEW = 13
    FOREIGN_UID = 14


class MigrateError(Exception):
    """Ошибка миграции с кодом возврата."""

    def __init__(self, code: ExitCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Каталоги, которые копируются. `locks/` намеренно отсутствует: лок не
# переносится (v0.6 §6.4).
COPY_DIRS = ("store", "epishu", "state", "data")

# Исключается из копирования всегда.
SKIP_NAMES = {"locks", "tmp"}

STATE_FILE = "migration.json"
REPORT_FILE = "migration-report.json"


@dataclass
class MigrateReport:
    state: str
    source: str
    target: str
    source_digest: str = ""
    target_digest: str = ""
    content_digest_source: str = ""
    content_digest_target: str = ""
    metadata_digest_target: str = ""
    files: int = 0
    dirs: int = 0
    bytes_copied: int = 0
    started_at: str = ""
    finished_at: str = ""
    mode: str = ""
    target_uid: int = -1
    target_gid: int = -1
    adopted_uids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fsync_dir(path: Path) -> None:
    """fsync каталога. Без этого запись метаданных каталога может не дойти."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        for name in filenames:
            full = here / name
            if full.is_symlink():
                continue
            try:
                fd = os.open(str(full), os.O_RDONLY)
            except OSError:
                continue
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        _fsync_dir(here)


def _write_state(run_dir: Path, payload: dict) -> None:
    """Атомарная запись маркера: tmp -> rename -> fsync файла, каталога, родителя."""
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / (STATE_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, run_dir / STATE_FILE)
    _fsync_dir(run_dir)
    _fsync_dir(run_dir.parent)


def _read_state(run_dir: Path) -> dict | None:
    p = run_dir / STATE_FILE
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _copytree_content(src: Path, dst: Path) -> tuple[int, int]:
    """Копировать содержимое без dereference симлинков, без locks/ и tmp/.

    Возвращает (файлов, байт). Абсолютная ссылка наружу источника — MigrateError(7).
    """
    dst.mkdir(parents=True, exist_ok=True)
    files = total = 0

    for dirpath, dirnames, filenames in os.walk(src):
        here = Path(dirpath)
        rel = here.relative_to(src)
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]

        for d in dirnames:
            target_dir = dst / rel / d
            target_dir.mkdir(parents=True, exist_ok=True)

        for name in filenames:
            s = here / name
            t = dst / rel / name
            st = os.lstat(s)

            if stat.S_ISLNK(st.st_mode):
                link_target = os.readlink(s)
                if os.path.isabs(link_target):
                    raise MigrateError(
                        ExitCode.NO_RULE,
                        f"absolute symlink not supported: {rel / name} -> {link_target}",
                    )
                if t.exists() or t.is_symlink():
                    t.unlink()
                os.symlink(link_target, t)
                continue

            if not stat.S_ISREG(st.st_mode):
                raise MigrateError(
                    ExitCode.NO_RULE,
                    f"unsupported file type: {rel / name}",
                )

            if st.st_nlink > 1:
                raise MigrateError(
                    ExitCode.NO_RULE, f"hard link not supported: {rel / name}"
                )

            shutil.copyfile(s, t, follow_symlinks=False)
            os.chmod(t, stat.S_IMODE(st.st_mode))
            files += 1
            total += st.st_size

    return files, total


def _normalize_permissions(root: Path) -> None:
    """Привести права к §4.7: 0700 каталоги, 0600 файлы, снять setuid/setgid."""
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        os.chmod(here, 0o700)
        for name in filenames:
            full = here / name
            if full.is_symlink():
                continue
            os.chmod(full, 0o600)


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    """Смена владельца без exec: os.lchown, симлинки не разыменовываются."""
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        os.lchown(here, uid, gid)
        for name in filenames:
            os.lchown(here / name, uid, gid)


def _foreign_uids(root: Path, allowed: set[int]) -> list[str]:
    bad: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        for name in list(dirnames) + list(filenames):
            full = here / name
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if st.st_uid not in allowed:
                bad.append(f"{full.relative_to(root)} (uid={st.st_uid})")
    return bad


@dataclass
class MigratePlan:
    source_var: Path
    target_var: Path
    run_dir: Path
    target_uid: int
    target_gid: int
    root_assisted: bool = False
    adopt_uids: list[int] = field(default_factory=list)
    allow_outside_home: bool = False


def _preflight(plan: MigratePlan) -> None:
    src = plan.source_var
    tgt = plan.target_var

    if not src.is_dir():
        raise MigrateError(ExitCode.BAD_INVOCATION, f"source is not a directory: {src}")

    if tgt.exists() and any(tgt.iterdir()):
        raise MigrateError(
            ExitCode.BAD_INVOCATION, f"target is not empty: {tgt}"
        )

    # Запуск от root без явного флага — отказ: привилегия не применяется молча.
    if os.geteuid() == 0 and not plan.root_assisted:
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            "running as root requires --root-assisted",
        )
    # Запрещено присваивать данные root как ЦЕЛЬ, когда запуск идёт от
    # непривилегированного пользователя (эскалация). Если сам процесс уже
    # root и target-uid тоже root — это не смена владельца, а текущий режим.
    if plan.root_assisted and plan.target_uid == 0 and os.geteuid() != 0:
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            "--target-uid 0 is forbidden for a non-root caller",
        )

    # Проверка владельцев до копирования: дешевле отказать сразу.
    allowed = {plan.target_uid}
    if plan.root_assisted:
        allowed.add(0)
    allowed.update(plan.adopt_uids)
    bad = _foreign_uids(src, allowed)
    if bad:
        raise MigrateError(
            ExitCode.FOREIGN_UID,
            "files with disallowed uid: " + ", ".join(bad[:5]),
        )

    # Staging должен быть на той же ФС, что целевой var.
    staging_parent = tgt.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    if os.stat(staging_parent).st_dev != os.stat(tgt.parent).st_dev:
        raise MigrateError(
            ExitCode.UNSUPPORTED_FS, "staging and target are on different filesystems"
        )


def migrate(plan: MigratePlan, *, force_restart: bool = False) -> MigrateReport:
    """Выполнить миграцию. Идемпотентна: повторный запуск продолжает или откатывает."""
    state = _read_state(plan.run_dir)

    if state is not None and not force_restart:
        return _resume(plan, state)

    _preflight(plan)

    staging = plan.target_var.parent / f"var.migrating-{int(time.time())}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    report = MigrateReport(
        state="prepared",
        source=str(plan.source_var),
        target=str(plan.target_var),
        started_at=_now(),
        mode="root-assisted" if plan.root_assisted else "rootless",
        target_uid=plan.target_uid,
        target_gid=plan.target_gid,
        adopted_uids=list(plan.adopt_uids),
    )
    _write_state(plan.run_dir, {
        "state": "prepared",
        "staging": str(staging),
        "target": str(plan.target_var),
        "source": str(plan.source_var),
        "started_at": report.started_at,
        "mode": report.mode,
    })

    # prepared -> copying
    _write_state(plan.run_dir, {**_read_state(plan.run_dir), "state": "copying"})
    report.state = "copying"

    try:
        for name in COPY_DIRS:
            s = plan.source_var / name
            if not s.is_dir():
                continue
            files, total = _copytree_content(s, staging / name)
            report.files += files
            report.bytes_copied += total

        _fsync_tree(staging)
    except MigrateError:
        _write_state(plan.run_dir, {**_read_state(plan.run_dir), "state": "failed:copy"})
        raise

    # copying -> verifying
    _write_state(plan.run_dir, {**_read_state(plan.run_dir), "state": "verifying"})
    report.state = "verifying"

    try:
        report.content_digest_source = content_digest(plan.source_var)
        report.content_digest_target = content_digest(staging)
    except DigestError as exc:
        _write_state(plan.run_dir, {**_read_state(plan.run_dir), "state": "failed:digest"})
        raise MigrateError(ExitCode.NO_RULE, str(exc)) from exc

    if report.content_digest_source != report.content_digest_target:
        _write_state(plan.run_dir, {**_read_state(plan.run_dir), "state": "failed:digest"})
        raise MigrateError(
            ExitCode.DATA_DIGEST,
            f"content digest mismatch: {report.content_digest_source} != {report.content_digest_target}",
        )

    # Нормализация прав ДО сравнения метаданных: цель обязана иметь
    # нормализованные права, а не права источника (v0.6 §7.4).
    if plan.root_assisted:
        _chown_tree(staging, plan.target_uid, plan.target_gid)
    _normalize_permissions(staging)
    if plan.root_assisted:
        _chown_tree(staging, plan.target_uid, plan.target_gid)

    report.metadata_digest_target = metadata_digest(staging)

    # verifying -> publishing (intent записан ДО rename)
    _write_state(plan.run_dir, {**_read_state(plan.run_dir), "state": "publishing"})
    report.state = "publishing"

    plan.target_var.parent.mkdir(parents=True, exist_ok=True)
    if plan.target_var.exists():
        shutil.rmtree(plan.target_var)
    os.replace(staging, plan.target_var)
    _fsync_dir(plan.target_var.parent)

    # publishing -> published
    _write_state(plan.run_dir, {**_read_state(plan.run_dir), "state": "published"})
    report.state = "published"

    report.target_digest = content_digest(plan.target_var)
    if report.target_digest != report.content_digest_source:
        raise MigrateError(
            ExitCode.DATA_DIGEST,
            "published tree digest differs from source",
        )

    report.source_digest = report.content_digest_source
    report.finished_at = _now()

    # Порядок важен: сначала переводим маркер в reported, затем пишем отчёт.
    # Иначе отчёт содержит state=published и не отражает фактическое завершение.
    report.state = "reported"
    _write_state(plan.run_dir, {**_read_state(plan.run_dir), "state": "reported"})

    (plan.run_dir / REPORT_FILE).write_text(
        json.dumps(report.__dict__, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    _fsync_dir(plan.run_dir)
    return report


def _resume(plan: MigratePlan, state: dict) -> MigrateReport:
    """Восстановление по правилу v0.6 §7.2 — все комбинации var/staging/манифест."""
    where = state.get("state", "")
    staging = Path(state.get("staging", ""))
    target = plan.target_var

    if where in ("reported",):
        report_path = plan.run_dir / REPORT_FILE
        data = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        return MigrateReport(**{k: v for k, v in data.items() if k in MigrateReport.__dataclass_fields__})

    if where == "published":
        # rename сделан, отчёт не записан — дописать.
        return _finish_report(plan, state)

    if where == "publishing":
        has_var = target.exists() and any(target.iterdir())
        has_staging = staging.exists()

        if has_var and not has_staging:
            # Манифест совпал? Проверяем фактический digest опубликованного дерева.
            expected = state.get("content_digest")
            actual = content_digest(target)
            if expected and actual != expected:
                _write_state(plan.run_dir, {**state, "state": "failed:corrupt_publish"})
                raise MigrateError(
                    ExitCode.DATA_DIGEST,
                    "published tree digest does not match manifest",
                )
            return _finish_report(plan, state)

        if not has_var and has_staging:
            return migrate(plan, force_restart=True)

        if has_var and has_staging:
            raise MigrateError(
                ExitCode.IO_ERROR,
                "both var and staging exist: manual resolution required",
            )
        raise MigrateError(
            ExitCode.IO_ERROR, "neither var nor staging exists: manual resolution required"
        )

    if where in ("prepared", "copying", "verifying"):
        return migrate(plan, force_restart=True)

    if where.startswith("failed:"):
        raise MigrateError(
            ExitCode.IO_ERROR, f"previous attempt failed ({where}); pass --restart"
        )

    return migrate(plan, force_restart=True)


def _finish_report(plan: MigratePlan, state: dict) -> MigrateReport:
    report = MigrateReport(
        state="reported",
        source=state.get("source", str(plan.source_var)),
        target=str(plan.target_var),
        started_at=state.get("started_at", ""),
        finished_at=_now(),
        mode=state.get("mode", ""),
        notes=["report reconstructed after crash between rename and report"],
    )
    report.content_digest_target = content_digest(plan.target_var)
    report.source_digest = state.get("content_digest", report.content_digest_target)
    report.target_digest = report.content_digest_target

    (plan.run_dir / REPORT_FILE).write_text(
        json.dumps(report.__dict__, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    _fsync_dir(plan.run_dir)
    _write_state(plan.run_dir, {**state, "state": "reported"})
    return report
