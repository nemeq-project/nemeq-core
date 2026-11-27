"""Установка Nemeq (NEMEQ_HOME_LAYOUT v0.6 §2.1, §4, §5).

Ключевой принцип: **preflight до создания каталогов**. Если хост не подходит,
отказ с кодом 2 и ни одного созданного каталога — иначе полуустановка.

Хостовые предпосылки проверяются явно (§2.1), а не выясняются падением на
старте: CPython, архитектура, glibc, системные библиотеки, ФС.

Принцип self-contained (§2.1а): никаких сетевых обращений. Отсутствие pip —
норма, а не повод падать.
"""
from __future__ import annotations

import ctypes.util
import json
import os
import platform
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .layout import (
    PathError,
    Resolved,
    config_dir,
    instance_root,
    lib_dir,
    lock_path,
    logs_dir,
    resolve_all,
    run_dir,
    tmp_dir,
)

EXIT_BAD_INVOCATION = 2
EXIT_UNSUPPORTED_FS = 8

REQUIRED_PYTHON = (3, 12)  # минимальная версия (>=3.12)
REQUIRED_ARCH = "x86_64"
INSTALL_MANIFEST = "INSTALL.json"
# find_library принимает имя БЕЗ префикса lib: "ssl" -> libssl.so.3
DEPENDENCY_PROBE = {"ssl": "ssl", "crypto": "crypto", "ffi": "ffi"}

# ФС, на которых не работают гарантии TEXTSTORE (flock/rename/fsync).
UNSAFE_FS = ("nfs", "nfs4", "cifs", "smb3", "overlay", "fuse.sshfs", "9p")


@dataclass
class PreflightResult:
    ok: bool
    checks: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checks": self.checks}


def _chk(checks: list[dict], name: str, ok: bool, detail: str = "") -> bool:
    checks.append({"check": name, "ok": ok, "detail": detail})
    return ok


def _fs_type(path: Path) -> str:
    """Тип ФС через statfs magic: без внешних утилит (§2.3)."""
    try:
        st = os.statvfs(str(path))
    except OSError:
        return "unknown"
    # statfs f_type через ctypes — переносимо и не требует df/mount
    try:
        import ctypes

        class Statfs(ctypes.Structure):
            _fields_ = [
                ("f_type", ctypes.c_long),
                ("f_bsize", ctypes.c_long),
                ("f_blocks", ctypes.c_ulong),
                ("f_bfree", ctypes.c_ulong),
                ("f_bavail", ctypes.c_ulong),
                ("f_files", ctypes.c_ulong),
                ("f_ffree", ctypes.c_ulong),
                ("f_fsid", ctypes.c_int * 2),
                ("f_namelen", ctypes.c_long),
                ("f_frsize", ctypes.c_long),
                ("f_flags", ctypes.c_long),
                ("f_spare", ctypes.c_long * 4),
            ]

        buf = Statfs()
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.statfs(str(path).encode(), ctypes.byref(buf)) == 0:
            magic = buf.f_type & 0xFFFFFFFF
            known = {
                0xEF53: "ext4",
                0x58465342: "xfs",
                0x9123683E: "btrfs",
                0x6969: "nfs",
                0xFF534D42: "cifs",
                0x794C7630: "overlay",
                0x01021994: "tmpfs",
                0x2FC12FC1: "zfs",
            }
            return known.get(magic, f"magic:{magic:#x}")
    except Exception:
        pass
    return "unknown"


def preflight(
    resolved: Resolved,
    *,
    env: dict[str, str] | None = None,
    min_glibc: str | None = None,
) -> PreflightResult:
    """Проверить хост ДО создания каталогов (§2.1б)."""
    environ = os.environ if env is None else env
    checks: list[dict] = []
    ok = True

    # 1. CPython >= 3.12 (верхняя граница не фиксируется: 3.13/3.14 совместимы
    # для pure-python кода; жёсткий пин ломал бы работу на новых хостах).
    vi = sys.version_info
    py_ok = (vi.major, vi.minor) >= REQUIRED_PYTHON
    ok &= _chk(checks, "python", py_ok,
               f"found {vi.major}.{vi.minor}, required >= "
               f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}")

    # 2. архитектура
    arch = platform.machine()
    ok &= _chk(checks, "arch", arch == REQUIRED_ARCH, f"found {arch}")

    # 3. glibc
    libc_name, libc_ver = platform.libc_ver()
    glibc_ok = libc_name == "glibc" and _version_ge(libc_ver, min_glibc or "2.28")
    ok &= _chk(checks, "glibc", glibc_ok,
               f"found {libc_name} {libc_ver}, required >= {min_glibc or '2.28'}")

    # 4. системные библиотеки
    for module, soname in DEPENDENCY_PROBE.items():
        found = ctypes.util.find_library(soname)
        ok &= _chk(checks, f"lib:{soname}", bool(found), found or "not found")

    # 5. ФС: каталог, где будет стор, обязан быть локальным
    probe = resolved.home
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    fs = _fs_type(probe)
    fs_ok = fs not in UNSAFE_FS
    ok &= _chk(checks, "filesystem", fs_ok, f"{probe}: {fs}")

    # 6. длина путей до сокетов (< 100, §4.6)
    longest = len(str(run_dir(resolved.home, resolved.instance)))
    ok &= _chk(checks, "socket_path_len", longest <= 100, f"{longest} chars")

    # 7. свободное место (>= 2 * max_state_bytes)
    try:
        usage = shutil.disk_usage(str(probe))
        ok &= _chk(checks, "disk_space", usage.free > 10 * 1024 * 1024,
                   f"{usage.free // (1024 * 1024)} MiB free")
    except OSError as exc:
        ok &= _chk(checks, "disk_space", False, str(exc))

    return PreflightResult(ok=ok, checks=checks)


def _version_ge(found: str, required: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        out = []
        for chunk in v.split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            if not digits:
                break
            out.append(int(digits))
        return tuple(out)

    a, b = parts(found), parts(required)
    return a >= b if a and b else False


def install(
    resolved: Resolved,
    *,
    min_glibc: str | None = None,
    nemeq_version: str = "0.1.0",
    schema_version: int = 4,
) -> dict:
    """Установить инстанс. Preflight строго до создания каталогов."""
    pf = preflight(resolved, min_glibc=min_glibc)
    if not pf.ok:
        failed = [c for c in pf.checks if not c["ok"]]
        raise _InstallError(
            EXIT_BAD_INVOCATION,
            "preflight failed: " + "; ".join(f"{c['check']}: {c['detail']}" for c in failed),
            pf.as_dict(),
        )

    # Только теперь создаём каталоги (§2.1б: «до создания каталогов»).
    from .instance import InstanceError, create, load  # локальный импорт: цикл

    # Идемпотентность (§8.8.2): повторный install поверх существующего HOME
    # сохраняет идентичность и не плодит инстансы. Поэтому «уже существует» —
    # это не ошибка, а загрузка существующего.
    try:
        info = create(resolved)
        created = True
    except InstanceError:
        info = load(resolved)
        created = False

    home = resolved.home
    lib = lib_dir(home)
    lib.mkdir(parents=True, exist_ok=True)
    os.chmod(lib, 0o700)

    manifest = {
        "layout_version": 6,
        "nemeq_version": nemeq_version,
        "schema_version": schema_version,
        "platform_tag": f"cp{REQUIRED_PYTHON[0]}{REQUIRED_PYTHON[1]}-{platform.machine()}",
        "deps_lock_sha256": "",
        "min_glibc": min_glibc or "2.28",
        "build_id": f"local-{int(time.time())}",
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    mpath = lib / INSTALL_MANIFEST
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    os.chmod(mpath, 0o600)

    # Байткод компилируется в lib/ при install (§4.3), дальше запись запрещена.
    _compile_bytecode(lib)

    return {
        "installed": info.as_dict(),
        "manifest": manifest,
        "preflight": pf.as_dict(),
        "created": created,
    }


def _compile_bytecode(lib: Path) -> None:
    """Предкомпиляция .pyc в lib/ при install — иначе runtime попытается писать."""
    import compileall

    compileall.compile_dir(str(lib), quiet=2, force=True, legacy=False)


class _InstallError(Exception):
    def __init__(self, code: int, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def read_manifest(home: Path) -> dict:
    p = lib_dir(home) / INSTALL_MANIFEST
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def check_schema_compat(home: Path, store_schema: int) -> None:
    """Стор со схемой новее поддерживаемой открывать нельзя (§4.5)."""
    manifest = read_manifest(home)
    supported = int(manifest.get("schema_version", 0))
    if supported and store_schema > supported:
        raise _InstallError(
            EXIT_BAD_INVOCATION,
            f"store schema {store_schema} > supported {supported}",
        )


InstallError = _InstallError


def run_cli_install(
    *,
    home: str | None = None,
    instance: str | None = None,
    state_root: str | None = None,
    min_glibc: str | None = None,
) -> tuple[int, dict]:
    """Слой для `nqctl install`."""
    try:
        resolved = resolve_all(
            home=home, instance=instance, state_root=state_root,
            allow_outside_home=True,
        )
    except PathError as exc:
        return EXIT_BAD_INVOCATION, {"title": "Invalid", "detail": str(exc)}

    try:
        return 0, install(resolved, min_glibc=min_glibc)
    except _InstallError as exc:
        return exc.code, {
            "title": "InstallFailed",
            "status": exc.code,
            "detail": exc.message,
            "preflight": exc.details,
        }
