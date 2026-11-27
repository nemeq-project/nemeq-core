"""Диагностика установки (NEMEQ_HOME_LAYOUT v0.6 §8.5–8.8).

`doctor` — машинно-читаемый артефакт приёмки (§8.8.7): JSON, на который можно
ссылаться в отчёте, а не «посмотрел глазами».

Проверки:

  * права: `var/` 0700, конфиги 0600, UID всех файлов = владелец (§4.7);
  * ФС: `var/tmp` и `var/epishu` на одном устройстве (§4.4), не NFS;
  * длина пути до сокета ≤ 100 (§4.6);
  * свободное место против `max_state_bytes` (§4.7);
  * манифест установки и совместимость схемы (§4.5);
  * песочница: фактические флаги из `/proc/self/status` (§6.5, best-effort);
  * симлинк `var` (если есть) указывает строго на `instances/default/var` (§8.9).
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .layout import Resolved, instance_root, lib_dir, lock_path, run_dir
from .install import read_manifest

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_BAD_INVOCATION = 2

DEFAULT_MAX_STATE_BYTES = 10 * 1024 ** 3  # 10 GiB
SOCKET_PATH_LIMIT = 100


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str = ""
    severity: str = "error"  # error | warning

    def as_dict(self) -> dict:
        return {"check": self.check, "ok": self.ok,
                "detail": self.detail, "severity": self.severity}


@dataclass
class DoctorReport:
    findings: list[Finding] = field(default_factory=list)
    hardened: bool = False

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings if f.severity == "error")

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "hardened": self.hardened,
            "findings": [f.as_dict() for f in self.findings],
        }


def _add(rep: DoctorReport, check: str, ok: bool, detail: str = "",
         severity: str = "error") -> None:
    rep.findings.append(Finding(check, ok, detail, severity))


def _mode(path: Path) -> int:
    try:
        return stat.S_IMODE(os.lstat(path).st_mode)
    except OSError:
        return -1


def _owner(path: Path) -> int:
    try:
        return os.lstat(path).st_uid
    except OSError:
        return -1


def check_permissions(resolved: Resolved, rep: DoctorReport) -> None:
    """§4.7: var 0700, конфиги 0600, UID = владелец HOME."""
    var_mode = _mode(resolved.var)
    _add(rep, "perm:var", var_mode == 0o700, f"{resolved.var}: {oct(var_mode)}")

    inst_cfg = instance_root(resolved.home, resolved.instance) / "config"
    cfg_file = inst_cfg / "instance.toml"
    if cfg_file.is_file():
        m = _mode(cfg_file)
        _add(rep, "perm:instance.toml", m == 0o600, f"{cfg_file.name}: {oct(m)}")

    expecting = _owner(resolved.home) if resolved.home.exists() else -1
    if expecting >= 0:
        wrong = []
        for dirpath, dirnames, filenames in os.walk(resolved.home):
            for name in list(dirnames) + list(filenames):
                full = Path(dirpath) / name
                uid = _owner(full)
                if uid >= 0 and uid != expecting:
                    wrong.append(f"{full} uid={uid}")
        _add(rep, "perm:owner", not wrong,
             "; ".join(wrong[:3]) if wrong else f"all files owned by uid {expecting}")


def _st_dev(path: Path) -> int:
    """Устройство, на котором лежит путь.

    Отдельная функция, а не инлайн `os.stat`: тест §4.4 обязан уметь показать
    расхождение ФС без `mount` (в контейнере без CAP_SYS_ADMIN его нет).
    """
    return os.stat(path).st_dev


def check_filesystems(resolved: Resolved, rep: DoctorReport) -> None:
    """§4.4: var/tmp и var/epishu на одном устройстве.

    Разные устройства означают `EXDEV` на `rename` из staging в стор. Проверка
    идёт по `st_dev`, а не по типу ФС: тип не различает две точки монтирования
    одного типа.
    """
    tmp = resolved.var / "tmp"
    epishu = resolved.var / "epishu"
    if tmp.exists() and epishu.exists():
        try:
            tmp_dev = _st_dev(tmp)
            epishu_dev = _st_dev(epishu)
        except OSError as exc:
            _add(rep, "fs:same_device", False, str(exc))
            return
        same = tmp_dev == epishu_dev
        _add(rep, "fs:same_device", same,
             f"tmp={tmp_dev} epishu={epishu_dev}")
    else:
        _add(rep, "fs:same_device", True, "not created yet", severity="warning")


def check_socket_path(resolved: Resolved, rep: DoctorReport) -> None:
    """§4.6: путь до сокета < 100 байт."""
    rd = run_dir(resolved.home, resolved.instance)
    length = len(str(rd))
    _add(rep, "path:socket_len", length <= SOCKET_PATH_LIMIT, f"{length} chars")


def effective_quota(probe: Path, max_state_bytes: int | None) -> int:
    """Квота, против которой проверяется место.

    Явно заданная квота проверяется БУКВАЛЬНО: если она больше диска, это
    ошибка конфигурации, а не «места хватает».

    Дефолтная квота выводится из диска (`min(DEFAULT, total // 4)`): жёсткие
    10 GiB в дефолте делали `doctor` вечно красным на машине с диском 7 GiB,
    хотя установка требует считанные мегабайты. Из-за этого §8.8.7
    («doctor --json как артефакт») не выполнялся ни на одном обычном хосте.
    """
    if max_state_bytes is not None:
        return int(max_state_bytes)
    try:
        total = os.statvfs(str(probe)).f_blocks * os.statvfs(str(probe)).f_frsize
    except OSError:
        return DEFAULT_MAX_STATE_BYTES
    return int(min(DEFAULT_MAX_STATE_BYTES, max(total // 4, 64 * 1024 ** 2)))


def check_disk(resolved: Resolved, rep: DoctorReport,
               max_state_bytes: int | None = None) -> None:
    """§4.7: свободное место под квоту `max_state_bytes`."""
    probe = resolved.home
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        st = os.statvfs(str(probe))
        free = st.f_bavail * st.f_frsize
    except OSError as exc:
        _add(rep, "disk:space", False, str(exc))
        return
    quota = effective_quota(probe, max_state_bytes)
    need = 2 * quota
    _add(rep, "disk:space", free >= need,
         f"{free // (1024**2)} MiB free, need {need // (1024**2)} MiB")


def check_manifest(resolved: Resolved, rep: DoctorReport) -> None:
    """§4.5: манифест установки на месте и читается."""
    manifest = read_manifest(resolved.home)
    _add(rep, "manifest:present", bool(manifest),
         f"layout_version={manifest.get('layout_version')}" if manifest else "missing")
    if manifest:
        _add(rep, "manifest:layout", int(manifest.get("layout_version", 0)) == 6,
             f"layout_version={manifest.get('layout_version')}")


def check_sandbox(resolved: Resolved, rep: DoctorReport) -> None:
    """§6.5: фактические флаги песочницы (best-effort, не обещание)."""
    nonpriv = False
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("NoNewPrivs:"):
                    nonpriv = line.split(":")[1].strip() == "1"
                    break
    except OSError:
        pass

    lib_ro = _is_readonly_mount(lib_dir(resolved.home))
    rep.hardened = bool(nonpriv and lib_ro)
    _add(rep, "sandbox:NoNewPrivs", nonpriv,
         "enforced" if nonpriv else "not enforced (run via systemd unit)",
         severity="warning")
    _add(rep, "sandbox:lib_readonly", lib_ro,
         "ro" if lib_ro else "lib is writable (unit not applied)",
         severity="warning")


def _is_readonly_mount(path: Path) -> bool:
    try:
        target = str(path.resolve())
    except OSError:
        return False
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            for line in fh:
                if " ro," in line or " ro " in line:
                    if f" {target} " in line:
                        return True
    except OSError:
        return False
    return False


def check_var_symlink(resolved: Resolved, rep: DoctorReport) -> None:
    """§8.9: симлинк var (если есть) ведёт строго на instances/default/var."""
    link = resolved.home / "var"
    if not link.is_symlink():
        _add(rep, "symlink:var", True, "no symlink (ok)", severity="warning")
        return
    target = os.readlink(link)
    expected = str(Path("instances") / "default" / "var")
    ok = target.rstrip("/") == expected
    _add(rep, "symlink:var", ok, f"{target} (expected {expected})")


def check_lock(resolved: Resolved, rep: DoctorReport) -> None:
    """§6.4: lock-файл внутри var/locks, а не в store."""
    lp = lock_path(resolved.var, resolved.store, resolved.home)
    inside = str(lp).startswith(str(resolved.var / "locks")) or str(lp).startswith(str(resolved.home / "locks"))
    _add(rep, "lock:location", inside, str(lp))
    if lp.exists():
        m = _mode(lp)
        _add(rep, "lock:mode", m == 0o600, f"{oct(m)}", severity="warning")


def run_doctor(resolved: Resolved, *, max_state_bytes: int | None = None,
               hardened_required: bool = False) -> DoctorReport:
    """Полная диагностика. Возвращает машинно-читаемый отчёт."""
    rep = DoctorReport()
    if not resolved.home.exists():
        _add(rep, "home:exists", False, f"{resolved.home} missing — run `nqctl install`")
        return rep

    _add(rep, "home:exists", True, str(resolved.home))
    check_permissions(resolved, rep)
    check_filesystems(resolved, rep)
    check_socket_path(resolved, rep)
    check_disk(resolved, rep, max_state_bytes)
    check_manifest(resolved, rep)
    check_sandbox(resolved, rep)
    check_var_symlink(resolved, rep)
    check_lock(resolved, rep)

    if hardened_required and not rep.hardened:
        _add(rep, "sandbox:required", False,
             "hardened profile requested but sandbox not enforced")
    return rep


def run_cli_doctor(
    *,
    home: str | None = None,
    instance: str | None = None,
    state_root: str | None = None,
    store: str | None = None,
    max_state_bytes: int | None = None,
    hardened: bool = False,
) -> tuple[int, dict]:
    """Слой для `nqctl doctor`."""
    from .layout import PathError, resolve_all

    try:
        resolved = resolve_all(home=home, instance=instance, state_root=state_root,
                               store=store, allow_outside_home=True)
    except PathError as exc:
        return EXIT_BAD_INVOCATION, {"title": "Invalid", "detail": str(exc)}

    rep = run_doctor(resolved, max_state_bytes=max_state_bytes,
                     hardened_required=hardened)
    return (EXIT_OK if rep.ok else EXIT_PROBLEMS), rep.as_dict()
