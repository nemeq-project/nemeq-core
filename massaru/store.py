"""TEXTSTORE v0.1 — append-only JSONL journal + materialized state.

One line in ``log/<segment>.jsonl`` is one transaction::

    {"rev":N,"prev":"sha256:...","ts":"...","actor":"...",
     "requestId":"...","txn":[{"op":"put|del","key":"...","value":...}],
     "hash":"sha256:..."}

The hash covers ``{rev, prev, txn}``.  ``state/`` is a materialization,
``snap/`` accelerates restore, ``views/`` is for humans.  One writer holds
``lock`` via flock; a background writer performs 2-10 ms group commits and
answers only after fsync.  Torn tail in the last segment is trimmed on start;
a corrupt middle line fails fsck and blocks start.
"""

from __future__ import annotations

import copy
import csv
import fcntl
import hashlib
import json
import os
import tempfile
import threading
import time
import tomllib
from datetime import datetime, timezone
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .layout import epishu_dir, epishu_root, instance_file, safe_epishu_id

GENESIS_HASH = "sha256:" + "0" * 64
FORMAT = 1
LOCK_NAME = "lock"
META_NAME = "meta.json"
LOG_DIR = "log"
SNAP_DIR = "snap"
STATE_DIR = "state"
VIEWS_DIR = "views"
DEFAULT_SEGMENT_SIZE = 64 * 1024 * 1024
DEFAULT_COMMIT_WINDOW = 0.005


class StoreError(Exception):
    """Base store refusal."""


class StoreBusy(StoreError):
    """Another writer holds flock."""


class ConflictError(StoreError):
    """CAS failure: expectedRev does not match the object."""


class StoreCorrupt(StoreError):
    """Journal/hash-chain is corrupt; restore is required."""


class DurabilityError(StoreError):
    """The batch was written but not fsynced; not acknowledged."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash_bytes(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _txn_hash(rev: int, prev: str, txn: list[dict[str, Any]]) -> str:
    return _hash_bytes({"rev": rev, "prev": prev, "txn": txn})


def _safe_key(key: str) -> list[str]:
    if not isinstance(key, str) or not key or "\x00" in key:
        raise StoreError("key must be a non-empty string without NUL")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise StoreError(f"unsafe key: {key!r}")
    return parts


def _state_path(base: Path, key: str) -> Path:
    parts = _safe_key(key)
    return base / STATE_DIR / Path(*parts).with_suffix(".json")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@dataclass
class FsckReport:
    ok: bool
    rev: int
    last_hash: str
    errors: list[str] = field(default_factory=list)
    repaired: bool = False
    truncated_bytes: int = 0
    segments: int = 0
    transactions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rev": self.rev,
            "lastHash": self.last_hash,
            "errors": list(self.errors),
            "repaired": self.repaired,
            "truncatedBytes": self.truncated_bytes,
            "segments": self.segments,
            "transactions": self.transactions,
        }


@dataclass
class ScanResult:
    report: FsckReport
    state: dict[str, dict[str, Any]]
    txns: list[dict[str, Any]]
    request_cache: dict[str, Any]


def _log_segments(path: Path) -> list[Path]:
    return sorted((path / LOG_DIR).glob("*.jsonl"))


def scan_store(path: str | Path, repair: bool = False) -> ScanResult:
    """Scan the append-only log without taking the writer lock.

    A torn tail in the last segment is trimmed only with ``repair=True``.
    A corrupt middle line or a broken chain is reported, never repaired.
    """
    base = Path(path)
    segments = _log_segments(base)
    report = FsckReport(
        ok=True,
        rev=0,
        last_hash=GENESIS_HASH,
        segments=len(segments),
    )
    state: dict[str, dict[str, Any]] = {}
    txns: list[dict[str, Any]] = []
    requests: dict[str, Any] = {}
    expected_rev = 1
    expected_hash = GENESIS_HASH

    for segment_index, segment in enumerate(segments):
        is_last = segment_index == len(segments) - 1
        try:
            data = segment.read_bytes()
        except OSError as exc:
            report.errors.append(f"read {segment}: {exc}")
            break
        offset = 0
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                if not is_last:
                    report.errors.append(
                        f"torn tail in non-last segment {segment.name}"
                    )
                elif repair:
                    try:
                        with segment.open("r+b") as handle:
                            handle.truncate(offset)
                            handle.flush()
                            os.fsync(handle.fileno())
                        report.repaired = True
                        report.truncated_bytes += len(line)
                    except OSError as exc:
                        report.errors.append(f"cannot repair {segment}: {exc}")
                else:
                    report.errors.append(
                        f"torn tail in {segment.name} needs --repair"
                    )
                break
            if len(line) == 1:
                report.errors.append(f"empty line in {segment.name}")
                break
            try:
                record = json.loads(line[:-1].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                report.errors.append(f"invalid JSON at rev {expected_rev}: {exc}")
                break
            if not isinstance(record, dict):
                report.errors.append(f"record at rev {expected_rev} is not object")
                break
            try:
                rev = int(record["rev"])
                prev = str(record["prev"])
                txn = record["txn"]
                digest = str(record["hash"])
            except (KeyError, TypeError, ValueError) as exc:
                report.errors.append(f"bad record shape at rev {expected_rev}: {exc}")
                break
            if rev != expected_rev:
                report.errors.append(
                    f"rev mismatch: expected {expected_rev}, got {rev}"
                )
                break
            if prev != expected_hash:
                report.errors.append(f"prev mismatch at rev {rev}")
                break
            if not isinstance(txn, list) or not txn:
                report.errors.append(f"txn at rev {rev} is not a non-empty list")
                break
            if digest != _txn_hash(rev, prev, txn):
                report.errors.append(f"hash mismatch at rev {rev}")
                break
            try:
                for operation in txn:
                    if not isinstance(operation, dict):
                        raise StoreError("operation is not object")
                    op = operation.get("op")
                    key = operation.get("key")
                    _safe_key(key)
                    if op == "put":
                        value = operation.get("value")
                        if not isinstance(value, dict):
                            raise StoreError("put value is not object")
                        state[key] = copy.deepcopy(value)
                    elif op == "del":
                        state.pop(key, None)
                    else:
                        raise StoreError(f"unknown op {op!r}")
            except StoreError as exc:
                report.errors.append(f"bad txn at rev {rev}: {exc}")
                break
            request_id = record.get("requestId")
            if isinstance(request_id, str) and request_id:
                if txn and txn[-1].get("op") == "put":
                    requests[request_id] = copy.deepcopy(txn[-1]["value"])
                else:
                    requests[request_id] = None
            txns.append(record)
            expected_rev += 1
            expected_hash = digest
            offset += len(line)
        if report.errors:
            break

    report.rev = expected_rev - 1
    report.last_hash = expected_hash
    report.transactions = len(txns)
    report.ok = not report.errors

    # Verify the latest snapshot if present; snapshots are not truth.
    snap_dir = base / SNAP_DIR
    snapshots = sorted(snap_dir.glob("*.json")) if snap_dir.is_dir() else []
    if snapshots and report.ok:
        try:
            snap = json.loads(snapshots[-1].read_text(encoding="utf-8"))
            snap_rev = int(snap["rev"])
            snap_hash = str(snap["hash"])
            if snap_rev > report.rev:
                report.errors.append("snapshot rev beyond journal")
            elif snap_rev > 0 and txns[snap_rev - 1]["hash"] != snap_hash:
                report.errors.append("snapshot hash does not match journal")
            elif snap_rev == 0 and snap_hash != GENESIS_HASH:
                report.errors.append("snapshot genesis hash mismatch")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report.errors.append(f"invalid snapshot: {exc}")
        report.ok = not report.errors

    return ScanResult(report, state, txns, requests)


class Store:
    """Single-writer JSONL store with group commit and CAS."""

    def __init__(
        self,
        path: str | Path,
        segment_size: int = DEFAULT_SEGMENT_SIZE,
        commit_window: float = DEFAULT_COMMIT_WINDOW,
    ) -> None:
        if not 0.002 <= float(commit_window) <= 0.010:
            raise ValueError("commit_window must be in [0.002, 0.010] seconds")
        if int(segment_size) <= 0:
            raise ValueError("segment_size must be positive")
        self.path = Path(path)
        self.segment_size = int(segment_size)
        self.commit_window = float(commit_window)
        self.path.mkdir(parents=True, exist_ok=True)
        for name in (LOG_DIR, SNAP_DIR, STATE_DIR, VIEWS_DIR):
            (self.path / name).mkdir(parents=True, exist_ok=True)

        self._lock_fd = os.open(
            str(self.path / LOCK_NAME), os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lock_fd)
            self._lock_fd = -1
            raise StoreBusy(f"another writer holds {self.path / LOCK_NAME}") from exc

        scan = scan_store(self.path, repair=True)
        if not scan.report.ok:
            os.close(self._lock_fd)
            self._lock_fd = -1
            raise StoreCorrupt("; ".join(scan.report.errors))
        self._state = scan.state
        self._txns = scan.txns
        self._request_cache = scan.request_cache
        self._rev = scan.report.rev
        self._last_hash = scan.report.last_hash
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._pending: deque[_Request] = deque()
        self._stopping = False
        self._closed = False
        self._busy = threading.Event()
        self._busy.set()
        self._stats = {"commits": 0, "ops": 0, "max_batch": 0, "fsyncs": 0}
        self._write_meta()
        self._materialize_all()
        self._write_views()
        self._writer = threading.Thread(
            target=self._writer_loop,
            name="nemeq-store-writer",
            daemon=True,
        )
        self._writer.start()

    # -- lifecycle -------------------------------------------------------
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
        if self._lock_fd >= 0:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = -1
        self._closed = True

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    # -- reads -----------------------------------------------------------
    def get(self, key: str) -> dict[str, Any] | None:
        self._check_open()
        _safe_key(key)
        with self._lock:
            value = self._state.get(key)
            return copy.deepcopy(value) if value is not None else None

    def list(
        self,
        prefix: str = "",
        label_selector: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        self._check_open()
        if prefix and "\x00" in prefix:
            raise StoreError("invalid prefix")
        with self._lock:
            keys = sorted(
                key for key in self._state if key.startswith(prefix)
            )
            values = [copy.deepcopy(self._state[key]) for key in keys]
        if label_selector:
            filtered = []
            for value in values:
                labels = value.get("labels", {})
                if isinstance(labels, dict) and all(
                    labels.get(k) == v for k, v in label_selector.items()
                ):
                    filtered.append(value)
            return filtered
        return values

    def watch(
        self, from_rev: int = 0, timeout: float | None = None
    ) -> Iterator[dict[str, Any]]:
        self._check_open()
        if not isinstance(from_rev, int) or from_rev < 0:
            raise StoreError("from_rev must be a non-negative int")
        cursor = from_rev + 1
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._check_open()
            with self._lock:
                pending = [
                    copy.deepcopy(txn)
                    for txn in self._txns
                    if int(txn["rev"]) >= cursor
                ]
            for txn in pending:
                yield txn
                cursor = int(txn["rev"]) + 1
            if deadline is not None and time.monotonic() >= deadline:
                return
            time.sleep(0.005)

    # -- writes ----------------------------------------------------------
    def _submit(self, request: "_Request") -> Any:
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
        key: str,
        value: dict[str, Any],
        expected_rev: int | None = None,
        request_id: str | None = None,
        actor: str = "",
    ) -> dict[str, Any]:
        _safe_key(key)
        if not isinstance(value, dict):
            raise StoreError("value must be an object")
        return self._submit(
            _Request(
                kind="put",
                key=key,
                value=copy.deepcopy(value),
                expected_rev=expected_rev,
                request_id=request_id,
                actor=actor,
            )
        )

    def delete(
        self,
        key: str,
        expected_rev: int | None = None,
        request_id: str | None = None,
        actor: str = "",
    ) -> None:
        _safe_key(key)
        self._submit(
            _Request(
                kind="delete",
                key=key,
                value=None,
                expected_rev=expected_rev,
                request_id=request_id,
                actor=actor,
            )
        )
        return None

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
        state = dict(self._state)
        rev = self._rev
        last_hash = self._last_hash
        lines: list[bytes] = []
        new_txns: list[dict[str, Any]] = []
        changed: set[str] = set()
        cache = dict(self._request_cache)
        accepted: list[_Request] = []

        for request in batch:
            if request.request_id and request.request_id in cache:
                request.result = copy.deepcopy(cache[request.request_id])
                continue
            current = state.get(request.key)
            current_rev = int(current.get("resourceVersion", 0)) if current else 0
            if (
                request.expected_rev is not None
                and request.expected_rev != current_rev
            ):
                request.error = ConflictError(
                    f"expected rev {request.expected_rev}, current {current_rev}"
                )
                continue
            rev += 1
            if request.kind == "delete":
                if current is None:
                    request.result = None
                    continue
                txn = [{"op": "del", "key": request.key}]
                state.pop(request.key, None)
                request.result = None
            else:
                stored = copy.deepcopy(request.value or {})
                uid = (
                    str(current.get("uid"))
                    if current and current.get("uid")
                    else str(stored.get("uid") or uuid.uuid4().hex)
                )
                stored["uid"] = uid
                stored["resourceVersion"] = rev
                txn = [{"op": "put", "key": request.key, "value": stored}]
                state[request.key] = stored
                request.result = stored
            if request.request_id:
                cache[request.request_id] = copy.deepcopy(request.result)
            record = {
                "rev": rev,
                "prev": last_hash,
                "ts": datetime.now(timezone.utc).isoformat(),
                "actor": request.actor,
                "requestId": request.request_id or "",
                "txn": txn,
                "hash": _txn_hash(rev, last_hash, txn),
            }
            lines.append(_canonical(record) + b"\n")
            new_txns.append(record)
            last_hash = record["hash"]
            changed.add(request.key)
            accepted.append(request)

        if not lines:
            return
        segment = self._current_segment()
        if segment.exists() and segment.stat().st_size > 0 and (
            segment.stat().st_size + sum(len(line) for line in lines)
            > self.segment_size
        ):
            segment = self._next_segment()
        original_size = segment.stat().st_size if segment.exists() else 0
        try:
            with segment.open("ab") as handle:
                for line in lines:
                    handle.write(line)
                handle.flush()
                self._fsync_fd(handle.fileno())
        except OSError as exc:
            try:
                with segment.open("r+b") as handle:
                    handle.truncate(original_size)
                    handle.flush()
                    self._fsync_fd(handle.fileno())
            except OSError:
                pass
            for request in accepted:
                request.error = DurabilityError(
                    f"fsync failed for rev {rev}: {exc}"
                )
            return

        with self._lock:
            self._state = state
            self._rev = rev
            self._last_hash = last_hash
            self._txns.extend(new_txns)
            self._request_cache = cache
        self._stats["fsyncs"] += 1
        self._write_meta()
        self._materialize(changed, state)
        try:
            self._after_fsync()
        except BaseException as exc:
            for request in accepted:
                if request.error is None and request.result is not None:
                    request.error = exc if isinstance(exc, Exception) else StoreError(str(exc))
            return
        for request in accepted:
            request.result = copy.deepcopy(request.result)

    def _after_fsync(self) -> None:
        """Test seam: a crash here happens after durability, before response."""

    # -- disk helpers ----------------------------------------------------
    def _current_segment(self) -> Path:
        segments = _log_segments(self.path)
        return segments[-1] if segments else self.path / LOG_DIR / "000001.jsonl"

    def _next_segment(self) -> Path:
        segments = _log_segments(self.path)
        if not segments:
            return self.path / LOG_DIR / "000001.jsonl"
        try:
            number = int(segments[-1].stem) + 1
        except ValueError as exc:
            raise StoreCorrupt(f"bad segment name: {segments[-1]}") from exc
        return self.path / LOG_DIR / f"{number:06d}.jsonl"

    def _fsync_fd(self, fd: int) -> None:
        os.fsync(fd)

    def _write_meta(self) -> None:
        _write_atomic(
            self.path / META_NAME,
            _canonical(
                {
                    "rev": self._rev,
                    "last_hash": self._last_hash,
                    "format": FORMAT,
                }
            )
            + b"\n",
        )

    def _materialize(
        self, changed: Iterable[str], state: dict[str, dict[str, Any]]
    ) -> None:
        for key in changed:
            path = _state_path(self.path, key)
            if key in state:
                _write_atomic(path, _canonical(state[key]) + b"\n")
            elif path.exists():
                path.unlink()

    def _materialize_all(self) -> None:
        for key, value in self._state.items():
            _write_atomic(
                _state_path(self.path, key), _canonical(value) + b"\n"
            )

    def _write_views(self) -> None:
        views = self.path / VIEWS_DIR
        index_lines = ["# Nemeq state", "", "| key | rev |", "|---|---:|"]
        for key in sorted(self._state):
            index_lines.append(
                f"| {key} | {int(self._state[key].get('resourceVersion', 0))} |"
            )
        _write_atomic(
            views / "index.md",
            ("\n".join(index_lines) + "\n").encode("utf-8"),
        )
        audit_path = views / "audit.csv"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".audit.", dir=str(audit_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["rev", "prev", "ts", "actor", "requestId", "hash"]
                )
                for txn in self._txns:
                    writer.writerow(
                        [
                            txn["rev"],
                            txn["prev"],
                            txn["ts"],
                            txn.get("actor", ""),
                            txn.get("requestId", ""),
                            txn["hash"],
                        ]
                    )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, audit_path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    # -- snapshots / fsck ------------------------------------------------
    def snapshot(self) -> int:
        self._check_open()
        if not self._busy.wait(timeout=5.0):
            raise StoreError("writer did not become idle")
        with self._lock:
            rev = self._rev
            last_hash = self._last_hash
            state = copy.deepcopy(self._state)
        payload = {
            "rev": rev,
            "hash": last_hash,
            "state": state,
            "ts": time.time(),
        }
        _write_atomic(
            self.path / SNAP_DIR / f"{rev:010d}.json",
            _canonical(payload) + b"\n",
        )
        snapshots = sorted((self.path / SNAP_DIR).glob("*.json"))
        for old in snapshots[:-2]:
            old.unlink()
        self._write_views()
        return rev

    def fsck(self, repair: bool = False) -> FsckReport:
        return scan_store(self.path, repair=repair).report

    def verify(self) -> bool:
        report = self.fsck(repair=False)
        if not report.ok:
            raise StoreCorrupt("; ".join(report.errors))
        return True


@dataclass
class _Request:
    kind: str
    key: str
    value: dict[str, Any] | None
    expected_rev: int | None
    request_id: str | None
    actor: str
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


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
