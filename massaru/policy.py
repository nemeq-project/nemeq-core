"""Принципы §2: самодостаточность, allow-list утилит, барьер sys.path.

Три независимых механизма, каждый проверяется отдельно:

  * **§2.2 нет сети** — поставка не имеет права ходить в сеть ни при install,
    ни при запуске. Проверяется перехватом сокетов, а не «на глаз»;
  * **§2.3 allow-list** — разрешены только `systemctl`/`systemctl --user`;
    `cp`, `rsync`, `sha256sum`, `unzip`, `tar`, `chown` запрещены, всё
    внутрипроцессно;
  * **§2.4 барьер sys.path** — путь импорта ограничен `lib/nemeq-core` и
    `lib/pydeps`; хост-site-packages не подмешиваются.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

EXIT_BAD_INVOCATION = 2

# Разрешённые внешние команды (§2.3). Всё остальное — внутрипроцессно.
ALLOWED_COMMANDS: frozenset[str] = frozenset({"systemctl"})

# Запрещённые явно: их подменяют модулями стандартной библиотеки.
FORBIDDEN_COMMANDS: frozenset[str] = frozenset({
    "cp", "rsync", "sha256sum", "unzip", "tar", "chown", "mv", "rm",
})


class PolicyError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- §2.2 сети

class NetworkBlocked(Exception):
    """Попытка сетевого вызова в запрещённом контексте."""


class network_guard:
    """Контекст, в котором любой сетевой вызов падает.

    Нужен для проверки принципа «установка без сети — штатный режим»:
    если код попытается соединиться, тест это увидит, а не будет ждать
    таймаут.
    """

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._orig_socket = None
        self._orig_create = None

    def __enter__(self) -> "network_guard":
        guard = self

        class BlockedSocket(socket.socket):
            def connect(self, address):  # type: ignore[override]
                guard.attempts.append(f"connect {address}")
                raise NetworkBlocked(f"network is forbidden in this context: {address}")

            def connect_ex(self, address):  # type: ignore[override]
                guard.attempts.append(f"connect_ex {address}")
                raise NetworkBlocked(f"network is forbidden in this context: {address}")

        self._orig_socket = socket.socket
        self._orig_create = socket.create_connection

        def blocked_create_connection(address, *a, **k):
            guard.attempts.append(f"create_connection {address}")
            raise NetworkBlocked(
                f"network is forbidden in this context: {address}"
            )

        socket.socket = BlockedSocket  # type: ignore[assignment]
        socket.create_connection = blocked_create_connection  # type: ignore[assignment]
        return self

    def __exit__(self, *exc_info) -> None:
        if self._orig_socket is not None:
            socket.socket = self._orig_socket  # type: ignore[assignment]
        if self._orig_create is not None:
            socket.create_connection = self._orig_create  # type: ignore[assignment]


def assert_no_network_attempts(attempts: list[str]) -> None:
    """Проверка результата guard: попыток быть не должно."""
    if attempts:
        raise PolicyError(
            EXIT_BAD_INVOCATION,
            f"network attempts during offline operation: {attempts[:3]}",
        )


def pip_absent(env: dict[str, str] | None = None) -> bool:
    """Отсутствие pip — норма (§2.1а), а не повод падать."""
    environ = os.environ if env is None else env
    for name in ("pip", "pip3", "ensurepip"):
        for part in environ.get("PATH", "").split(os.pathsep):
            if part and (Path(part) / name).is_file():
                return False
    return True


# ------------------------------------------------------------ §2.3 утилиты

def command_name(argv: list[str] | str) -> str:
    if isinstance(argv, str):
        return argv.split()[0] if argv.split() else ""
    return Path(argv[0]).name if argv else ""


def check_command(argv: list[str] | str) -> None:
    """Разрешить только allow-list. Запрещённая утилита — ошибка."""
    name = command_name(argv)
    if not name:
        raise PolicyError(EXIT_BAD_INVOCATION, "empty command")
    if name in FORBIDDEN_COMMANDS:
        raise PolicyError(
            EXIT_BAD_INVOCATION,
            f"command {name!r} is forbidden (§2.3): use a stdlib call instead",
        )
    if name not in ALLOWED_COMMANDS:
        raise PolicyError(
            EXIT_BAD_INVOCATION,
            f"command {name!r} is not in allow-list {sorted(ALLOWED_COMMANDS)} (§2.3)",
        )


def run_allowed(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Запуск только через проверку allow-list."""
    check_command(argv)
    return subprocess.run(argv, **kwargs)  # noqa: S603 - проверено выше


def copy_file(src: Path, dst: Path) -> None:
    """Копирование внутрипроцессно: shutil, а не вызов `cp` (§2.3)."""
    import shutil

    shutil.copy2(src, dst)


def hash_file(path: Path) -> str:
    """sha256 через hashlib, не через `sha256sum` (§2.3)."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def chown(path: Path, uid: int, gid: int, follow: bool = False) -> None:
    """Смена владельца через os, не через утилиту `chown` (§2.3)."""
    if follow:
        os.chown(path, uid, gid)
    else:
        os.lchown(path, uid, gid)


# --------------------------------------------------------- §2.4 sys.path

def allowed_import_roots(home: Path) -> list[str]:
    """Разрешённые корни импорта: только lib/nemeq-core и lib/pydeps."""
    lib = home / "lib"
    return [str(lib / "nemeq-core"), str(lib / "pydeps")]


def hostile_path_entries(home: Path) -> list[str]:
    """Записи sys.path, которых быть не должно.

    Хост-site-packages и пользовательский `.local` не подмешиваются:
    иначе импорт может прийти не из поставки, и «self-contained» ложно.
    """
    allowed = {str(Path(p).resolve()) for p in allowed_import_roots(home)}
    bad: list[str] = []
    for entry in sys.path:
        if not entry:
            continue
        try:
            resolved = str(Path(entry).resolve())
        except OSError:
            continue
        if resolved in allowed:
            continue
        low = resolved.lower()
        if ("site-packages" in low or "dist-packages" in low
                or "/.local/lib" in low):
            bad.append(resolved)
    return bad


def sanitize_path(home: Path) -> list[str]:
    """Очистить sys.path до разрешённых корней (§2.4). Возвращает удалённое."""
    roots = [(Path(p)).resolve() for p in allowed_import_roots(home)]
    removed: list[str] = []
    kept: list[str] = []
    for entry in sys.path:
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except OSError:
            removed.append(entry)
            continue
        if any(resolved == r or r in resolved.parents or resolved in r.parents
               for r in roots):
            kept.append(entry)
        else:
            removed.append(entry)
    sys.path[:] = kept
    return removed
