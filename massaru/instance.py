"""Жизненный цикл инстанса Nemeq (NEMEQ_HOME_LAYOUT v0.6 §6).

Отвечает за `create|list|status|destroy`. Ключевые инварианты:

  * `create` ставит права 0700 на каталоги и 0600 на конфиги (§4.7);
  * `destroy` требует остановленного сервиса и берёт лок стора; лок-файлы
    в `var/locks/` **не удаляются** (иначе unlink-lockfile race, §6.4);
  * `destroy` не трогает `lib/`;
  * `uninstall --purge` удаляет всё дерево `$NEMEQ_HOME` и только после
    доказательства отсутствия живых владельцев;
  * идентичность: `home_id` (префикс), `store_id` (данные), `instance_id`
    (инстанс) — все UUIDv4 (§6.1).

Строки legacy-путей здесь не используются: только резолверы `massaru.layout`.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import time
import uuid
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
)


class InstanceError(Exception):
    """Ошибка жизненного цикла с кодом возврата (§7.9)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


EXIT_BAD_INVOCATION = 2
EXIT_LOCK_BUSY = 4
EXIT_IDENTITY_MISMATCH = 5
EXIT_IO = 11

STATE_FILE = "instance.toml"
HOME_FILE = "home.toml"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_toml_simple(path: Path, data: dict) -> None:
    """Минимальный writer: плоский key = "value" / key = 123 / key = [..].

    Отдельная зависимость на toml не нужна (self-contained, §2.1).
    """
    lines: list[str] = []
    for key in sorted(data):
        value = data[key]
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        elif isinstance(value, list):
            inner = ", ".join(json.dumps(v) if isinstance(v, str) else str(v) for v in value)
            lines.append(f"{key} = [{inner}]")
        else:
            lines.append(f"{key} = {json.dumps(str(value))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_toml_simple(path: Path) -> dict:
    import re

    data: dict[str, object] = {}
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("["):
            items = re.findall(r'"((?:[^"\\]|\\.)*)"|(-?\d+)', raw)
            data[key] = [
                json.loads(f'"{s}"') if s else int(n) for s, n in items
            ]
        elif raw.startswith('"'):
            data[key] = json.loads(raw)
        elif raw in ("true", "false"):
            data[key] = raw == "true"
        else:
            try:
                data[key] = int(raw)
            except ValueError:
                data[key] = raw
    return data


def _try_lock(lock_file: Path) -> int | None:
    """Попытаться взять лок. Возвращает fd или None, если лок занят."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(lock_file), flags, 0o600)
    except OSError as exc:
        raise InstanceError(EXIT_IO, f"cannot open lock {lock_file}: {exc}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _read_lock_pid(lock_file: Path) -> int | None:
    try:
        text = lock_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class InstanceInfo:
    name: str
    home: str
    var: str
    store: str
    instance_id: str
    store_id: str
    home_id: str
    schema_version: int
    created_at: str
    port: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "home": self.home,
            "var": self.var,
            "store": self.store,
            "instanceId": self.instance_id,
            "storeId": self.store_id,
            "homeId": self.home_id,
            "schemaVersion": self.schema_version,
            "createdAt": self.created_at,
            "port": self.port,
        }


def _paths(resolved: Resolved) -> dict[str, Path]:
    return {
        "root": instance_root(resolved.home, resolved.instance),
        "var": resolved.var,
        "store": resolved.store,
        "logs": logs_dir(resolved.home, resolved.instance),
        "run": run_dir(resolved.home, resolved.instance),
    }


def ensure_home(resolved: Resolved) -> str:
    """Создать `home.toml`, если его нет. Идемпотентно: id не перегенерируется."""
    cfg = config_dir(resolved.home)
    cfg.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg, 0o700)
    home_file = cfg / HOME_FILE
    existing = _read_toml_simple(home_file)
    if existing.get("home_id"):
        return str(existing["home_id"])

    home_id = str(uuid.uuid4())
    _write_toml_simple(home_file, {
        "home_id": home_id,
        "layout_version": 6,
        "created_at": _now(),
    })
    os.chmod(home_file, 0o600)
    return home_id


def create(resolved: Resolved) -> InstanceInfo:
    """Создать инстанс. Права 0700 каталоги, 0600 конфиги (§4.7)."""
    home_id = ensure_home(resolved)
    paths = _paths(resolved)

    if (paths["root"] / "config" / STATE_FILE).is_file():
        raise InstanceError(
            EXIT_BAD_INVOCATION,
            f"instance {resolved.instance!r} already exists at {paths['root']}",
        )

    for key in ("root", "var", "store", "logs", "run"):
        paths[key].mkdir(parents=True, exist_ok=True)
        os.chmod(paths[key], 0o700)

    (paths["var"] / "locks").mkdir(parents=True, exist_ok=True)
    os.chmod(paths["var"] / "locks", 0o700)
    (paths["var"] / "tmp").mkdir(parents=True, exist_ok=True)
    os.chmod(paths["var"] / "tmp", 0o700)

    cfg = paths["root"] / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg, 0o700)

    info = InstanceInfo(
        name=resolved.instance,
        home=str(resolved.home),
        var=str(resolved.var),
        store=str(resolved.store),
        instance_id=str(uuid.uuid4()),
        store_id=str(uuid.uuid4()),
        home_id=home_id,
        schema_version=4,
        created_at=_now(),
        port=8448,
    )
    state = {
        "instance_id": info.instance_id,
        "store_id": info.store_id,
        "home_id": info.home_id,
        "schema_version": info.schema_version,
        "created_at": info.created_at,
        "port": info.port,
        "store": str(resolved.store),
        # lineage заполняется миграцией; здесь пусто
        "origin_home_id": "",
        "origin_instance_id": "",
        "origin_store_id": "",
        "migrated_at": "",
        "target_uid": os.getuid(),
        "target_gid": os.getgid(),
        "adopted_uids": [],
        "mode_normalized": True,
    }
    state_file = cfg / STATE_FILE
    _write_toml_simple(state_file, state)
    os.chmod(state_file, 0o600)
    return info


def load(resolved: Resolved) -> InstanceInfo:
    """Прочитать инстанс. Авторитет — instance.toml, не run/instance.json (§6.1)."""
    paths = _paths(resolved)
    state_file = paths["root"] / "config" / STATE_FILE
    if not state_file.is_file():
        raise InstanceError(
            EXIT_BAD_INVOCATION,
            f"instance {resolved.instance!r} not found at {paths['root']}",
        )
    data = _read_toml_simple(state_file)
    home_data = _read_toml_simple(config_dir(resolved.home) / HOME_FILE)

    if data.get("home_id") and home_data.get("home_id") and data["home_id"] != home_data["home_id"]:
        raise InstanceError(
            EXIT_IDENTITY_MISMATCH,
            f"home_id mismatch: instance={data['home_id']} home={home_data['home_id']}",
        )

    return InstanceInfo(
        name=resolved.instance,
        home=str(resolved.home),
        var=str(resolved.var),
        store=str(resolved.store),
        instance_id=str(data.get("instance_id", "")),
        store_id=str(data.get("store_id", "")),
        home_id=str(data.get("home_id", "")),
        schema_version=int(data.get("schema_version", 0)),
        created_at=str(data.get("created_at", "")),
        port=int(data.get("port", 0)),
    )


def list_instances(home: Path) -> list[InstanceInfo]:
    """Перечислить инстансы префикса через `instances/*/config/instance.toml`."""
    base = home / "instances"
    if not base.is_dir():
        return []
    out: list[InstanceInfo] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        state_file = child / "config" / STATE_FILE
        if not state_file.is_file():
            continue
        data = _read_toml_simple(state_file)
        out.append(InstanceInfo(
            name=child.name,
            home=str(home),
            var=str(child / "var"),
            store=str(data.get("store", child / "var" / "store")),
            instance_id=str(data.get("instance_id", "")),
            store_id=str(data.get("store_id", "")),
            home_id=str(data.get("home_id", "")),
            schema_version=int(data.get("schema_version", 0)),
            created_at=str(data.get("created_at", "")),
            port=int(data.get("port", 0)),
        ))
    return out


def status(resolved: Resolved) -> dict:
    """Статус инстанса: пути, идентичность, факт занятости лока."""
    info = load(resolved)
    lock_file = lock_path(resolved.var, resolved.store)
    fd = _try_lock(lock_file)
    running = fd is None
    pid = _read_lock_pid(lock_file) if running else None
    if fd is not None:
        os.close(fd)

    return {
        **info.as_dict(),
        "lock": str(lock_file),
        "running": running,
        "pid": pid,
        "sources": resolved.sources,
    }


def destroy(resolved: Resolved) -> dict:
    """Удалить данные инстанса. Требует остановленного сервиса (§6.3).

    Удаляются `var/{store,epishu,state,data}` и `config/`, `logs/`, `run/`.
    НЕ удаляются: `lib/` (общий) и `var/locks/` (иначе unlink-lockfile race).
    """
    paths = _paths(resolved)
    load(resolved)  # инстанс обязан существовать

    lock_file = lock_path(resolved.var, resolved.store)
    fd = _try_lock(lock_file)
    if fd is None:
        pid = _read_lock_pid(lock_file)
        raise InstanceError(
            EXIT_LOCK_BUSY,
            f"store busy: pid {pid if pid is not None else 'unknown'} (stop the service first)",
        )

    removed: list[str] = []
    try:
        for name in ("store", "epishu", "state", "data", "tmp"):
            target = resolved.var / name
            if target.exists():
                shutil.rmtree(target, ignore_errors=False)
                removed.append(str(target))

        for key in ("run", "logs"):
            target = paths[key]
            if target.exists():
                shutil.rmtree(target, ignore_errors=False)
                removed.append(str(target))

        cfg = paths["root"] / "config"
        if cfg.exists():
            shutil.rmtree(cfg, ignore_errors=False)
            removed.append(str(cfg))

        # var/ и var/locks/ остаются намеренно: лок не удаляется (§6.4).
    finally:
        os.close(fd)

    return {
        "destroyed": resolved.instance,
        "removed": removed,
        "kept": [str(lib_dir(resolved.home)), str(lock_file)],
    }


def uninstall(resolved: Resolved, purge: bool = False) -> dict:
    """`uninstall [--purge]`.

    Без `--purge` — удаляет только данные инстанса (§ destroy).
    С `--purge` — всё дерево `$NEMEQ_HOME`, но лишь после доказательства,
    что живых владельцев нет (взять лок каждого стора + проверить pid).
    """
    if not purge:
        return destroy(resolved)

    home = resolved.home
    instances = list_instances(home)

    for inst in instances:
        var = Path(inst.var)
        store = Path(inst.store)
        lock_file = lock_path(var, store)
        fd = _try_lock(lock_file)
        if fd is None:
            pid = _read_lock_pid(lock_file)
            raise InstanceError(
                EXIT_LOCK_BUSY,
                f"refusing purge: instance {inst.name!r} store busy "
                f"(pid {pid if pid is not None else 'unknown'})",
            )
        os.close(fd)

    removed = str(home)
    shutil.rmtree(home, ignore_errors=False)
    return {"purged": removed, "instances": [i.name for i in instances]}


def run_cli_action(
    action: str,
    *,
    home: str | None = None,
    instance: str | None = None,
    state_root: str | None = None,
    store: str | None = None,
    purge: bool = False,
) -> tuple[int, dict]:
    """Слой для `nqctl instance <action>`. Возвращает (код, JSON-полезная нагрузка)."""
    try:
        resolved = resolve_all(
            home=home, instance=instance,
            state_root=state_root, store=store,
            allow_outside_home=True,
        )
    except PathError as exc:
        return EXIT_BAD_INVOCATION, {"title": "Invalid", "detail": str(exc)}

    try:
        if action == "create":
            return 0, create(resolved).as_dict()
        if action == "list":
            return 0, {"instances": [i.as_dict() for i in list_instances(resolved.home)]}
        if action == "status":
            return 0, status(resolved)
        if action == "destroy":
            return 0, destroy(resolved)
        if action == "uninstall":
            return 0, uninstall(resolved, purge=purge)
        return EXIT_BAD_INVOCATION, {"title": "Invalid", "detail": f"unknown action {action!r}"}
    except InstanceError as exc:
        return exc.code, {"title": "Error", "status": exc.code, "detail": exc.message}
