"""Переход уже развёрнутых alpha/beta (§7.10, закрывает B5).

В v0.3 `instance_id` не мог одновременно идентифицировать стор и быть
уникальным. Блок описывает переход от старой раскладки, где HOME был один
на инстанс, к новой, где один HOME держит много инстансов.

Ключевое отличие от обычной миграции (§7.1–7.4): источник здесь — не `var/`,
а целый HOME с `home.toml` и `instance.toml`. Поэтому порядок иной:

  1. прочитать манифесты источника;
  2. `--use-lib-from` обязателен и не может указывать на источник (код 2) —
     иначе версия берётся из мигрируемого HOME, что и было противоречием v0.3;
  3. `schema_version` источника против поддерживаемой выбранным lib/ → выше: код 5;
  4. `nemeq_version` расходится → код 13, флага обхода нет;
  5. копируется только `var/`;
  6. `store_id` сохраняется, `instance_id` новый, `lineage` заполняется;
  7. коллизия `store_id` → код 5 с обоими путями и сравнением digest;
  8. каждый HOME мигрируется отдельно; слияние двух HOME → код 2.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .digest import content_digest
from .layout import lock_path
from .migrate import ExitCode, MigrateError

# Минимальная поддерживаемая схема выбранным lib/ (§7.10 п.3).
SUPPORTED_SCHEMA = 4


@dataclass
class SourceManifest:
    """То, что читается из источника до любых операций с файлами."""

    home: Path
    layout_version: int
    nemeq_version: str
    schema_version: int
    store_id: str
    instance_name: str
    var: Path


@dataclass
class LegacyPlan:
    """План перехода одного HOME в инстанс."""

    from_home: Path
    into_instance: str
    use_lib_from: Path
    target_home: Path
    run_dir: Path
    target_uid: int = -1
    target_gid: int = -1
    adopt_uids: list[int] = field(default_factory=list)


@dataclass
class LegacyReport:
    state: str
    from_home: str
    into_instance: str
    store_id: str
    instance_id: str
    lineage: list[str]
    schema_version: int
    nemeq_version: str
    files: int = 0
    bytes_copied: int = 0
    started_at: str = ""
    finished_at: str = ""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_manifest(home: Path) -> SourceManifest:
    """Прочитать `home.toml` и `instance.toml` источника (§7.10 п.1).

    Манифесты читаются ДО любых операций: если их нет или они битые, отказ
    происходит раньше, чем что-либо скопировано.
    """
    home_file = home / "home.toml"
    if not home_file.is_file():
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            f"source HOME has no home.toml: {home}",
        )

    with open(home_file, "rb") as fh:
        home_data = tomllib.load(fh)

    # instance.toml лежит либо в корне HOME (v0.3: один HOME = один инстанс),
    # либо в instances/<name>/config/ (новая раскладка).
    inst_file = home / "instance.toml"
    if not inst_file.is_file():
        candidates = sorted((home / "instances").glob("*/config/instance.toml"))
        if not candidates:
            raise MigrateError(
                ExitCode.BAD_INVOCATION,
                f"source HOME has no instance.toml: {home}",
            )
        inst_file = candidates[0]

    with open(inst_file, "rb") as fh:
        inst_data = tomllib.load(fh)

    var = home / "var"
    if not var.is_dir():
        var = inst_file.parent.parent / "var"
    if not var.is_dir():
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            f"source HOME has no var/: {home}",
        )

    return SourceManifest(
        home=home,
        layout_version=int(home_data.get("layout_version", 0)),
        nemeq_version=str(inst_data.get("nemeq_version", "")),
        schema_version=int(inst_data.get("schema_version", 0)),
        store_id=str(inst_data.get("store_id", "")),
        instance_name=str(inst_data.get("name", inst_data.get("instance", ""))),
        var=var,
    )


def _is_same_path(a: Path, b: Path) -> bool:
    """Совпадение путей по realpath — иначе симлинк обойдёт запрет."""
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def check_lib_source(plan: LegacyPlan) -> None:
    """`--use-lib-from` обязателен и не может указывать на источник (§7.10 п.2)."""
    if plan.use_lib_from is None:
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            "--use-lib-from is required: version must come from the target lib, "
            "not from the migrated HOME",
        )
    if _is_same_path(plan.use_lib_from, plan.from_home):
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            f"--use-lib-from must not point at the source HOME: "
            f"{plan.use_lib_from} == {plan.from_home}",
        )
    if _is_same_path(plan.use_lib_from, plan.target_home):
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            f"--use-lib-from must not point at the target HOME: {plan.use_lib_from}",
        )
    if not plan.use_lib_from.is_dir():
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            f"--use-lib-from is not a directory: {plan.use_lib_from}",
        )


def check_versions(manifest: SourceManifest, plan: LegacyPlan) -> None:
    """Схема и версия (§7.10 п.3, п.4)."""
    supported = _lib_schema(plan.use_lib_from)
    if manifest.schema_version > supported:
        raise MigrateError(
            ExitCode.IDENTITY_MISMATCH,
            f"source schema_version {manifest.schema_version} is newer than "
            f"the selected lib supports ({supported})",
        )

    wanted = _lib_version(plan.use_lib_from)
    if wanted and manifest.nemeq_version and manifest.nemeq_version != wanted:
        raise MigrateError(
            ExitCode.VERSION_SKEW,
            f"nemeq_version mismatch: source {manifest.nemeq_version!r}, "
            f"lib {wanted!r} (no override flag)",
        )


def _lib_schema(lib: Path) -> int:
    """Схема, поддерживаемая выбранным lib/. Маркер — `lib/schema_version`."""
    marker = lib / "schema_version"
    if marker.is_file():
        try:
            return int(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    return SUPPORTED_SCHEMA


def _lib_version(lib: Path) -> str:
    """Версия lib/. Пусто — сверка не выполняется (нет данных, не «совпало»)."""
    marker = lib / "VERSION"
    if marker.is_file():
        try:
            return marker.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def find_store_collision(home: Path, store_id: str) -> Path | None:
    """Найти существующий инстанс с тем же `store_id` (§7.10 п.7)."""
    instances = home / "instances"
    if not instances.is_dir():
        return None
    for child in sorted(instances.iterdir()):
        cfg = child / "config" / "instance.toml"
        if not cfg.is_file():
            continue
        try:
            with open(cfg, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if str(data.get("store_id", "")) == store_id:
            return child
    return None


def check_collision(home: Path, manifest: SourceManifest, into_instance: str) -> None:
    """Коллизия `store_id` → код 5 с обоими путями и сравнением digest."""
    existing = find_store_collision(home, manifest.store_id)
    if existing is None:
        return
    if existing.name == into_instance:
        # Миграция в тот же инстанс — не коллизия, а повтор.
        return

    other_var = existing / "var"
    relation = "unknown"
    if other_var.is_dir():
        try:
            if content_digest(other_var) == content_digest(manifest.var):
                relation = "identical copy (same digest)"
            else:
                relation = "diverged (digest differs)"
        except OSError:
            relation = "digest unavailable"

    raise MigrateError(
        ExitCode.IDENTITY_MISMATCH,
        f"store_id {manifest.store_id!r} already exists in instance "
        f"{existing.name!r} ({other_var}); source {manifest.var}: {relation}",
    )


def _copytree(src: Path, dst: Path) -> tuple[int, int]:
    """Копировать дерево внутрипроцессно, без утилит (§2.3)."""
    files = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == "." else dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            s = Path(dirpath) / name
            t = target_dir / name
            shutil.copy2(s, t)
            files += 1
            try:
                total += t.stat().st_size
            except OSError:
                pass
    return files, total


def adopt(plan: LegacyPlan) -> LegacyReport:
    """Выполнить переход одного HOME в инстанс (§7.10).

    Проверки идут в порядке пунктов: манифесты → lib/ → схема → версия →
    коллизия → копирование. Любая проверка до копирования гарантирует, что
    отказ не оставляет полу-целевого инстанса.
    """
    started = _now()

    manifest = read_manifest(plan.from_home)
    check_lib_source(plan)
    check_versions(manifest, plan)
    check_collision(plan.target_home, manifest, plan.into_instance)

    target = plan.target_home / "instances" / plan.into_instance
    if target.exists():
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            f"target instance already exists: {target}",
        )

    target_var = target / "var"
    target_var.parent.mkdir(parents=True, exist_ok=True)
    files, total = _copytree(manifest.var, target_var)

    # store_id сохраняется, instance_id новый, lineage заполняется (§7.10 п.6).
    instance_id = str(uuid.uuid4())
    lineage: list[str] = []
    if manifest.instance_name:
        lineage.append(f"{manifest.instance_name}@store:{manifest.store_id}")
    if manifest.layout_version:
        lineage.append(f"layout-v{manifest.layout_version}")

    cfg_dir = target / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": plan.into_instance,
        "instance_id": instance_id,
        "store_id": manifest.store_id,
        "schema_version": manifest.schema_version,
        "nemeq_version": manifest.nemeq_version,
        "lineage": lineage,
        "adopted_from": str(plan.from_home),
        "adopted_at": _now(),
    }
    cfg_file = cfg_dir / "instance.toml"
    cfg_file.write_text(_toml(cfg), encoding="utf-8")
    os.chmod(cfg_file, 0o600)

    return LegacyReport(
        state="adopted",
        from_home=str(plan.from_home),
        into_instance=plan.into_instance,
        store_id=manifest.store_id,
        instance_id=instance_id,
        lineage=lineage,
        schema_version=manifest.schema_version,
        nemeq_version=manifest.nemeq_version,
        files=files,
        bytes_copied=total,
        started_at=started,
        finished_at=_now(),
    )


def _toml(data: dict) -> str:
    """Минимальный сериализатор: плоские скаляры и список строк."""
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            items = ", ".join(json.dumps(str(v), ensure_ascii=False) for v in value)
            lines.append(f"{key} = [{items}]")
        elif isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        else:
            lines.append(f"{key} = {json.dumps(str(value), ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def merge_homes_refused(sources: list[Path]) -> None:
    """Слияние двух HOME в один инстанс запрещено (§7.10 п.8)."""
    if len(sources) > 1:
        raise MigrateError(
            ExitCode.BAD_INVOCATION,
            "each HOME must be migrated separately: "
            f"got {len(sources)} sources, merging into one instance is forbidden",
        )


def lock_for(plan: LegacyPlan) -> Path:
    """Лок целевого стора по §6.4 — переход не должен обгонять живого владельца."""
    return lock_path(plan.target_home / "instances" / plan.into_instance / "var",
                     plan.target_home / "instances" / plan.into_instance / "var" / "store",
                     plan.target_home)
