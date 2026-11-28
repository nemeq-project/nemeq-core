"""Runtime-состояние инстанса: `run/instance.json` и порты (v0.6 §6.1, §6.2).

Ключевое из спецификации:

  * авторитет идентичности — `instance.toml` и метаданные стора, НЕ runtime-файл;
  * порядок строго «лок → чтение → решение»: читать runtime-файл до взятия лока
    нельзя, иначе решение принимается по протухшим данным;
  * `run/instance.json` создаётся ПОСЛЕ успешного лока и перезаписывается
    атомарно; при успешном локе любой существующий файл считается протухшим
    (живых владельцев нет по определению лока);
  * порт по умолчанию фиксированный 8448; занят → код 3 с именем виновника,
    без автоинкремента (§6.2);
  * эфемерный порт 0 — явная опция, фактический порт публикуется в runtime-файле.
"""
from __future__ import annotations

import fcntl
import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from .layout import Resolved, lock_path, run_dir

EXIT_BAD_INVOCATION = 2
EXIT_PORT_BUSY = 3
EXIT_LOCK_BUSY = 4

DEFAULT_PORT = 8448
RUNTIME_FILE = "instance.json"


class RuntimeError_(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class RuntimeState:
    name: str
    home_id: str
    store_id: str
    instance_id: str
    pid: int
    port: int
    started_at: str
    store_realpath: str
    sandbox: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "homeId": self.home_id,
            "storeId": self.store_id,
            "instanceId": self.instance_id,
            "pid": self.pid,
            "port": self.port,
            "startedAt": self.started_at,
            "storeRealpath": self.store_realpath,
            "sandbox": self.sandbox,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def runtime_path(resolved: Resolved) -> Path:
    return run_dir(resolved.home, resolved.instance) / RUNTIME_FILE


def _try_lock(lock_file: Path) -> int | None:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(lock_file), flags, 0o600)
    except OSError as exc:
        raise RuntimeError_(EXIT_BAD_INVOCATION, f"cannot open lock: {exc}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """Проверить, свободен ли порт. 0 считается свободным (эфемерный)."""
    if port == 0:
        return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def find_port_owner(home: Path, port: int) -> str | None:
    """Найти инстанс, который держит порт, по его `run/instance.json`."""
    base = home / "instances"
    if not base.is_dir():
        return None
    for child in sorted(base.iterdir()):
        rt = child / "run" / RUNTIME_FILE
        if not rt.is_file():
            continue
        try:
            data = json.loads(rt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if int(data.get("port", -1)) == port:
            pid = int(data.get("pid", 0) or 0)
            return f"{child.name} (pid {pid})" if pid else child.name
    return None


def reserve_port(resolved: Resolved, desired: int | None = None) -> int:
    """Выбрать порт по §6.2. Занятый порт — код 3 с именем виновника.

    Автоинкремента НЕТ: молчаливая смена порта ломает клиентов, которые уже
    знают адрес. Эфемерный порт запрашивается явно как 0.
    """
    port = DEFAULT_PORT if desired is None else desired
    if port != 0 and not port_is_free(port):
        owner = find_port_owner(resolved.home, port)
        detail = f" (held by {owner})" if owner else ""
        raise RuntimeError_(
            EXIT_PORT_BUSY, f"port {port} is busy{detail}"
        )
    return port


def write_runtime(resolved: Resolved, state: RuntimeState) -> Path:
    """Атомарная запись runtime-файла: tmp -> rename -> fsync файла и каталога."""
    path = runtime_path(resolved)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)

    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state.as_dict(), fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)

    try:
        fd = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    return path


def read_runtime(resolved: Resolved) -> dict | None:
    """Прочитать runtime-файл. Только как отображение, не как источник истины."""
    path = runtime_path(resolved)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def start(
    resolved: Resolved,
    *,
    home_id: str,
    store_id: str,
    instance_id: str,
    desired_port: int | None = None,
    sandbox: dict | None = None,
) -> RuntimeState:
    """Старт инстанса по протоколу «лок → чтение → решение» (§6.1).

    1. взять лок стора — успех доказывает отсутствие живых владельцев;
    2. порт проверить ПОСЛЕ лока (пока держим лок, конкурент не займёт);
    3. runtime-файл перезаписать — он протух по определению успешного лока.
    """
    lock_file = lock_path(resolved.var, resolved.store, resolved.home)
    fd = _try_lock(lock_file)
    if fd is None:
        raise RuntimeError_(
            EXIT_LOCK_BUSY, f"store busy: {lock_file}"
        )

    try:
        port = reserve_port(resolved, desired_port)

        state = RuntimeState(
            name=resolved.instance,
            home_id=home_id,
            store_id=store_id,
            instance_id=instance_id,
            pid=os.getpid(),
            port=port,
            started_at=_now(),
            store_realpath=os.path.realpath(resolved.store),
            sandbox=sandbox or {},
        )
        write_runtime(resolved, state)
        return state
    finally:
        os.close(fd)


def reconcile_identity(
    resolved: Resolved,
    *,
    config_home_id: str,
    config_store_id: str,
    config_instance_id: str,
) -> dict:
    """Сверить идентичность: конфиг против runtime (§6.1).

    Авторитет — `instance.toml` (+ метаданные стора). Runtime-файл может
    отсутствовать или быть протухшим: расхождение `instance_id` НЕ блокирует
    старт, файл перезаписывается.
    """
    rt = read_runtime(resolved)
    result = {
        "authoritative": {
            "homeId": config_home_id,
            "storeId": config_store_id,
            "instanceId": config_instance_id,
        },
        "runtime": rt,
        "runtimeStale": rt is not None and rt.get("instanceId") != config_instance_id,
        "missing": rt is None,
    }
    return result
