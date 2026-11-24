"""LMDB-backed control-plane Store for Nemeq (APISERVER v0.3, D-11).

LMDB is the durability engine.  The logical journal lives in the same LMDB
transaction as the object and meta rows:

    objects: "obj\\0<resource>\\0<name>" -> JSON object
    changes: "chg\\0<rev:020d>"          -> JSON {rev,type,key,prevRev,hash,prevHash}
    meta:    "meta"                      -> JSON {rev,last_hash,format}

Every mutating batch is one LMDB write transaction with ``sync=True``.  A
background writer performs a 2-10 ms group commit; a caller receives its
result only after that commit.  Disabling LMDB sync is never used for mutations.

The public interface is deliberately LMDB-free: get/list/watch/put/delete.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import tomllib
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import lmdb

from .layout import epishu_dir, epishu_root, instance_file, safe_epishu_id

GENESIS_HASH = "sha256:" + "0" * 64
META_KEY = b"meta"
OBJECT_PREFIX = b"obj\x00"
CHANGE_PREFIX = b"chg\x00"
FORMAT = 1
DEFAULT_MAP_SIZE = 256 * 1024 * 1024
DEFAULT_COMMIT_WINDOW = 0.005


class StoreError(Exception):
    """Base store refusal."""


class ConflictError(StoreError):
    """CAS failure: expected rev does not match the current object."""


class StoreCorrupt(StoreError):
    """Logical journal/hash-chain is inconsistent."""


@dataclass
class _Request:
    kind: str
    resource: str
    name: str
    obj: dict[str, Any] | None
    expected_rev: int | None
    actor: str
    request_id: str
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _obj_key(resource: str, name: str) -> bytes:
    return OBJECT_PREFIX + resource.encode("utf-8") + b"\x00" + name.encode(
        "utf-8"
    )


def _change_key(rev: int) -> bytes:
    return CHANGE_PREFIX + f"{rev:020d}".encode("ascii")


def _change_hash(change: dict[str, Any]) -> str:
    body = {key: value for key, value in change.items() if key != "hash"}
    return _digest(body)


def _validate_key(resource: str, name: str) -> None:
    for value, label in ((resource, "resource"), (name, "name")):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise StoreError(f"{label} must be a non-empty string without NUL")


def _decode(raw: bytes | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreCorrupt(f"invalid JSON in store: {exc}") from exc


class Store:
    """Single-writer LMDB store with logical journal and group commit."""

    def __init__(
        self,
        path: str | Path,
        map_size: int = DEFAULT_MAP_SIZE,
        commit_window: float = DEFAULT_COMMIT_WINDOW,
    ) -> None:
        if not 0.002 <= float(commit_window) <= 0.010:
            raise ValueError("commit_window must be in [0.002, 0.010] seconds")
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.commit_window = float(commit_window)
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._pending: deque[_Request] = deque()
        self._stopping = False
        self._closed = False
        self._busy = threading.Event()
        self._busy.set()
        self._stats = {"commits": 0, "ops": 0, "max_batch": 0}
        try:
            self._env = lmdb.open(
                str(self.path),
                map_size=int(map_size),
                sync=True,
                metasync=True,
                lock=True,
                max_dbs=0,
            )
        except lmdb.Error as exc:
            raise StoreError(f"cannot open LMDB at {self.path}: {exc}") from exc
        try:
            self._ensure_meta()
            self.verify()
        except Exception:
            self._env.close()
            raise
        self._writer = threading.Thread(
            target=self._writer_loop,
            name="nemeq-store-writer",
            daemon=True,
        )
        self._writer.start()

    # -- lifecycle -------------------------------------------------------
    def _ensure_meta(self) -> None:
        with self._env.begin(write=True) as txn:
            if txn.get(META_KEY) is None:
                txn.put(
                    META_KEY,
                    _canonical(
                        {"rev": 0, "last_hash": GENESIS_HASH, "format": FORMAT}
                    ),
                )

    def _check_open(self) -> None:
        if self._closed:
            raise StoreError("store is closed")

    def close(self) -> None:
        if self._closed:
            return
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        self._writer.join(timeout=10.0)
        self._env.close()
        self._closed = True

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    # -- reads -----------------------------------------------------------
    def get(self, resource: str, name: str) -> dict[str, Any] | None:
        self._check_open()
        _validate_key(resource, name)
        with self._env.begin() as txn:
            value = _decode(txn.get(_obj_key(resource, name)))
        if value is None:
            return None
        if not isinstance(value, dict):
            raise StoreCorrupt("object row is not an object")
        return value

    def list(self, resource: str | None = None) -> list[dict[str, Any]]:
        self._check_open()
        if resource is not None and (
            not isinstance(resource, str) or "\x00" in resource
        ):
            raise StoreError("invalid resource")
        prefix = (
            OBJECT_PREFIX
            if resource is None
            else OBJECT_PREFIX + resource.encode("utf-8") + b"\x00"
        )
        result: list[dict[str, Any]] = []
        with self._env.begin() as txn:
            cursor = txn.cursor()
            if not cursor.set_range(prefix):
                return []
            for key, raw in cursor:
                if not key.startswith(prefix):
                    break
                value = _decode(raw)
                if not isinstance(value, dict):
                    raise StoreCorrupt("object row is not an object")
                result.append(value)
        return result

    def watch(
        self, from_rev: int = 0, timeout: float | None = None
    ) -> Iterator[dict[str, Any]]:
        self._check_open()
        if not isinstance(from_rev, int) or from_rev < 0:
            raise StoreError("from_rev must be a non-negative int")
        cursor_rev = from_rev + 1
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._check_open()
            changes: list[dict[str, Any]] = []
            with self._env.begin() as txn:
                meta = _decode(txn.get(META_KEY))
                if not isinstance(meta, dict):
                    raise StoreCorrupt("meta is missing")
                current = int(meta.get("rev", 0))
                for rev in range(cursor_rev, current + 1):
                    value = _decode(txn.get(_change_key(rev)))
                    if not isinstance(value, dict):
                        raise StoreCorrupt(f"change {rev} is missing")
                    changes.append(value)
            for change in changes:
                yield change
            if changes:
                cursor_rev = int(changes[-1]["rev"]) + 1
            if deadline is not None and time.monotonic() >= deadline:
                return
            time.sleep(0.005)

    # -- writes ----------------------------------------------------------
    def _submit(self, request: _Request) -> Any:
        self._check_open()
        with self._cond:
            self._pending.append(request)
            self._cond.notify()
        request.event.wait()
        if request.error is not None:
            raise request.error
        return request.result

    def put(
        self,
        resource: str,
        name: str,
        obj: dict[str, Any],
        expected_rev: int | None = None,
        actor: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        _validate_key(resource, name)
        if not isinstance(obj, dict):
            raise StoreError("object must be a dict")
        request = _Request(
            kind="put",
            resource=resource,
            name=name,
            obj=copy.deepcopy(obj),
            expected_rev=expected_rev,
            actor=actor,
            request_id=request_id,
        )
        return self._submit(request)

    def delete(
        self,
        resource: str,
        name: str,
        expected_rev: int | None = None,
        actor: str = "",
        request_id: str = "",
    ) -> bool:
        _validate_key(resource, name)
        request = _Request(
            kind="delete",
            resource=resource,
            name=name,
            obj=None,
            expected_rev=expected_rev,
            actor=actor,
            request_id=request_id,
        )
        return self._submit(request)

    # -- group commit ----------------------------------------------------
    def _writer_loop(self) -> None:
        while True:
            with self._cond:
                while not self._pending and not self._stopping:
                    self._cond.wait()
                if not self._pending and self._stopping:
                    return
                batch: list[_Request] = []
                if self._stopping:
                    while self._pending:
                        batch.append(self._pending.popleft())
                else:
                    deadline = time.monotonic() + self.commit_window
                    while True:
                        while self._pending:
                            batch.append(self._pending.popleft())
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._cond.wait(remaining)
            self._commit_batch(batch)

    def _commit_batch(self, batch: list[_Request]) -> None:
        self._busy.clear()
        try:
            self._commit_batch_inner(batch)
        finally:
            self._busy.set()
            with self._lock:
                self._stats["commits"] += 1
                self._stats["ops"] += len(batch)
                self._stats["max_batch"] = max(
                    self._stats["max_batch"], len(batch)
                )
            for request in batch:
                request.event.set()

    def _commit_batch_inner(self, batch: list[_Request]) -> None:
        txn = self._env.begin(write=True)
        try:
            meta = _decode(txn.get(META_KEY))
            if not isinstance(meta, dict):
                raise StoreCorrupt("meta is missing")
            state = {
                "rev": int(meta.get("rev", 0)),
                "last_hash": str(meta.get("last_hash", GENESIS_HASH)),
                "format": FORMAT,
            }
            for request in batch:
                self._apply_request(txn, state, request)
            txn.put(META_KEY, _canonical(state))
            txn.commit()
        except BaseException as exc:
            try:
                txn.abort()
            except Exception:
                pass
            for request in batch:
                if request.error is None:
                    request.error = (
                        exc if isinstance(exc, StoreError) else StoreError(str(exc))
                    )

    def _apply_request(
        self, txn: Any, state: dict[str, Any], request: _Request
    ) -> None:
        key = _obj_key(request.resource, request.name)
        current = _decode(txn.get(key))
        if current is not None and not isinstance(current, dict):
            raise StoreCorrupt("object row is not an object")
        current_rev = int(current.get("resourceVersion", 0)) if current else 0
        if (
            request.expected_rev is not None
            and request.expected_rev != current_rev
        ):
            request.error = ConflictError(
                f"expected rev {request.expected_rev}, current {current_rev}"
            )
            return

        if request.kind == "delete":
            if current is None:
                request.result = False
                return
            rev = state["rev"] + 1
            txn.delete(key)
            uid = str(current.get("uid", ""))
            object_hash = _digest(current)
            change = self._make_change(
                rev,
                "DELETED",
                request,
                uid,
                current_rev,
                state["last_hash"],
                object_hash,
            )
            txn.put(_change_key(rev), _canonical(change))
            state["rev"] = rev
            state["last_hash"] = change["hash"]
            request.result = True
            return

        if request.obj is None:
            raise StoreError("put requires an object")
        rev = state["rev"] + 1
        uid = (
            str(current.get("uid"))
            if current and current.get("uid")
            else str(request.obj.get("uid") or uuid.uuid4().hex)
        )
        stored = copy.deepcopy(request.obj)
        stored["name"] = request.name
        stored["uid"] = uid
        stored["resourceVersion"] = rev
        txn.put(key, _canonical(stored))
        change = self._make_change(
            rev,
            "ADDED" if current is None else "MODIFIED",
            request,
            uid,
            current_rev,
            state["last_hash"],
            _digest(stored),
        )
        txn.put(_change_key(rev), _canonical(change))
        state["rev"] = rev
        state["last_hash"] = change["hash"]
        request.result = stored

    @staticmethod
    def _make_change(
        rev: int,
        event_type: str,
        request: _Request,
        uid: str,
        prev_rev: int,
        prev_hash: str,
        object_hash: str,
    ) -> dict[str, Any]:
        body = {
            "rev": rev,
            "type": event_type,
            "key": f"{request.resource}/{request.name}",
            "resource": request.resource,
            "name": request.name,
            "uid": uid,
            "prevRev": prev_rev,
            "prevHash": prev_hash,
            "objectHash": object_hash,
            "actor": request.actor,
            "requestId": request.request_id,
            "ts": time.time(),
        }
        body["hash"] = _change_hash(body)
        return body

    # -- verification / backup ------------------------------------------
    def verify(self) -> bool:
        with self._env.begin() as txn:
            meta = _decode(txn.get(META_KEY))
            if not isinstance(meta, dict):
                raise StoreCorrupt("meta is missing")
            rev = int(meta.get("rev", 0))
            last_hash = str(meta.get("last_hash", GENESIS_HASH))
            expected_hash = GENESIS_HASH
            for change_rev in range(1, rev + 1):
                change = _decode(txn.get(_change_key(change_rev)))
                if not isinstance(change, dict):
                    raise StoreCorrupt(f"change {change_rev} is missing")
                if int(change.get("rev", -1)) != change_rev:
                    raise StoreCorrupt(f"change {change_rev} has wrong rev")
                if change.get("prevHash") != expected_hash:
                    raise StoreCorrupt(
                        f"change {change_rev} breaks hash-chain"
                    )
                if change.get("hash") != _change_hash(change):
                    raise StoreCorrupt(
                        f"change {change_rev} hash mismatch"
                    )
                expected_hash = str(change["hash"])
            if last_hash != expected_hash:
                raise StoreCorrupt("meta.last_hash does not match chain")
        return True

    def backup(self, destination: str | Path) -> None:
        self._check_open()
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        if any(destination.iterdir()):
            raise StoreError("backup destination must be empty")
        if not self._busy.wait(timeout=5.0):
            raise StoreError("writer did not become idle")
        try:
            self._env.copy(str(destination), compact=True)
        except lmdb.Error as exc:
            raise StoreError(f"LMDB backup failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Compatibility read-only instance lookup (massaru skeleton CLI)
# ---------------------------------------------------------------------------
def _read_instance(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def list_instances(state_root: Path) -> list[dict[str, Any]]:
    """List installed epishu directories that contain instance.toml."""
    root = epishu_root(state_root)
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        try:
            safe_epishu_id(child.name)
        except ValueError:
            continue
        instance = _read_instance(child / "instance.toml")
        if instance is None:
            continue
        result.append(
            {
                "epishu_id": child.name,
                "state": str(instance.get("state", "unknown")),
                "path": str(child),
            }
        )
    return result


def inspect_instance(
    state_root: Path, epishu_id: str
) -> dict[str, Any] | None:
    path = epishu_dir(state_root, epishu_id)
    instance = _read_instance(instance_file(state_root, epishu_id))
    if instance is None:
        return None
    return {
        "epishu_id": epishu_id,
        "path": str(path),
        "instance": instance,
    }
