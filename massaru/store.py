"""TEXTSTORE v0.2 — append-only JSONL journal with fail-stop commit.

One line is one transaction::

    {"fmt":1,"rev":N,"prev":"sha256:...","ts":"...","actor":"...",
     "requestId":"...","txn":[{"op":"put|del","key":"...","value":...}],
     "hash":"sha256:..."}

``hash = sha256(JCS({fmt,rev,prev,ts,actor,requestId,txn}))``.  ``state/`` is
materialization, ``snap/`` restore acceleration, ``anchor/`` signed off-host
anchors, ``views/`` human output.  One writer holds ``lock`` via flock; a
background writer batches for 2-10 ms and publishes only after fsync.

Write/fsync/ENOSPC/EIO failure: ftruncate to batchStartOffset, fsync, then
the store is read-only until ``fsck`` clears it.  ``committedOffset`` is the
visibility boundary.
"""

from __future__ import annotations

import copy
import csv
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import tomllib
import unicodedata
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .layout import epishu_dir, epishu_root, instance_file, safe_epishu_id

GENESIS_HASH = "sha256:" + "0" * 64
FORMAT = 1
LOCK_NAME = "lock"
META_NAME = "meta.json"
LOG_DIR = "log"
SNAP_DIR = "snap"
ANCHOR_DIR = "anchor"
STATE_DIR = "state"
VIEWS_DIR = "views"
ANCHOR_KEY = "anchor.key"
DEFAULT_SEGMENT_SIZE = 64 * 1024 * 1024
DEFAULT_COMMIT_WINDOW = 0.005
DEFAULT_WATCH_BUFFER = 10_000
MAX_VALUE_BYTES = 1 * 1024 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024
ANCHOR_INTERVAL = 1000
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}/[a-z0-9][a-z0-9.-]{0,62}$")


class StoreError(Exception):
    """Base store refusal; ``code`` maps to an API status."""

    code = 500


class StoreBusy(StoreError):
    code = 409


class ConflictError(StoreError):
    code = 409


class StoreCorrupt(StoreError):
    code = 500


class StoreReadOnly(StoreError):
    code = 503


class DurabilityError(StoreError):
    code = 500


class PayloadTooLarge(StoreError):
    code = 413


class InvalidRequest(StoreError):
    code = 400


class WatchGone(StoreError):
    code = 410

    def __init__(self, message: str, rev: int) -> None:
        super().__init__(message)
        self.rev = rev


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidRequest("object keys must be strings")
            result[_nfc(key)] = _normalize(item)
        return result
    return value


def _jcs(value: Any) -> bytes:
    """RFC 8785-style canonical JSON for the subset used by the store."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidRequest(f"not canonical JSON: {exc}") from exc
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidRequest(f"not valid UTF-8: {exc}") from exc


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_jcs(value)).hexdigest()


def _record_hash(record: dict[str, Any]) -> str:
    body = {
        "fmt": int(record["fmt"]),
        "rev": int(record["rev"]),
        "prev": str(record["prev"]),
        "ts": str(record["ts"]),
        "actor": str(record["actor"]),
        "requestId": str(record["requestId"]),
        "txn": record["txn"],
    }
    return _sha(body)


def _safe_key(key: str) -> str:
    key = _nfc(key)
    if not KEY_RE.match(key):
        raise InvalidRequest(f"invalid key: {key!r}")
    return key


def _state_path(base: Path, key: str) -> Path:
    resource, name = _safe_key(key).split("/", 1)
    return base / STATE_DIR / resource / f"{name}.json"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            dir_fd = os.open(
                path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _anchor_key_from_store(path: Path) -> bytes | None:
    env = os.environ.get("NEMEQ_ANCHOR_KEY")
    if env:
        return env.encode("utf-8")
    key_path = path / ANCHOR_KEY
    if key_path.is_file():
        try:
            return key_path.read_bytes()
        except OSError:
            return None
    return None


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
    committed_offset: int = 0
    segment: str = ""
    anchor_rev: int = 0

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
            "committedOffset": self.committed_offset,
            "segment": self.segment,
            "anchorRev": self.anchor_rev,
        }


@dataclass
class ScanResult:
    report: FsckReport
    state: dict[str, dict[str, Any]]
    txns: list[dict[str, Any]]
    request_cache: dict[str, Any]
    events: list[dict[str, Any]]
    anchor: dict[str, Any] | None


def _log_segments(path: Path) -> list[Path]:
    return sorted((path / LOG_DIR).glob("*.jsonl"))


def _latest_anchor(path: Path) -> dict[str, Any] | None:
    anchors = sorted((path / ANCHOR_DIR).glob("*.anchor"))
    if not anchors:
        return None
    try:
        payload = json.loads(anchors[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StoreCorrupt(f"cannot read anchor: {exc}") from exc
    if not isinstance(payload, dict):
        raise StoreCorrupt("anchor is not an object")
    return payload


def _verify_anchor(
    payload: dict[str, Any], key: bytes
) -> str | None:
    try:
        body = {
            "fmt": int(payload["fmt"]),
            "rev": int(payload["rev"]),
            "hash": str(payload["hash"]),
        }
        signature = str(payload["sig"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreCorrupt(f"bad anchor shape: {exc}") from exc
    expected = "hmac-sha256:" + hmac.new(
        key, _jcs(body), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return "anchor signature mismatch"
    return None


def scan_store(
    path: str | Path,
    repair: bool = False,
    anchor_key: bytes | None = None,
) -> ScanResult:
    """Scan journal without taking the writer lock."""
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
    events: list[dict[str, Any]] = []
    requests: dict[str, Any] = {}
    expected_rev = 1
    expected_hash = GENESIS_HASH
    committed_offset = 0
    committed_segment = segments[-1].name if segments else ""

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
            try:
                if _normalize(record) != record:
                    report.errors.append(f"non-NFC record at rev {expected_rev}")
                    break
                fmt = int(record.get("fmt", FORMAT))
                rev = int(record["rev"])
                prev = str(record["prev"])
                digest = str(record["hash"])
                txn = record["txn"]
            except (InvalidRequest, KeyError, TypeError, ValueError) as exc:
                report.errors.append(
                    f"bad record shape at rev {expected_rev}: {exc}"
                )
                break
            if fmt != FORMAT:
                report.errors.append(f"unsupported fmt {fmt} at rev {rev}")
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
            try:
                if digest != _record_hash(record):
                    report.errors.append(f"hash mismatch at rev {rev}")
                    break
            except (KeyError, TypeError, ValueError, InvalidRequest) as exc:
                report.errors.append(f"bad hash input at rev {rev}: {exc}")
                break
            if len(line) > MAX_LINE_BYTES:
                report.errors.append(f"line at rev {rev} exceeds 2 MiB")
                break
            try:
                for sequence, operation in enumerate(txn):
                    if not isinstance(operation, dict):
                        raise InvalidRequest("operation is not object")
                    op = operation.get("op")
                    key = _safe_key(operation.get("key"))
                    if op == "put":
                        value = operation.get("value")
                        if not isinstance(value, dict):
                            raise InvalidRequest("put value is not object")
                        event_type = "ADDED" if key not in state else "MODIFIED"
                        resource_version = str(value.get("resourceVersion", ""))
                        state[key] = copy.deepcopy(value)
                        events.append(
                            {
                                "rev": rev,
                                "seq": sequence,
                                "type": event_type,
                                "key": key,
                                "resourceVersion": resource_version,
                                "value": copy.deepcopy(value),
                            }
                        )
                    elif op == "del":
                        previous = state.pop(key, None)
                        events.append(
                            {
                                "rev": rev,
                                "seq": sequence,
                                "type": "DELETED",
                                "key": key,
                                "resourceVersion": (
                                    str(previous.get("resourceVersion", ""))
                                    if previous
                                    else ""
                                ),
                                "value": None,
                            }
                        )
                    else:
                        raise InvalidRequest(f"unknown op {op!r}")
            except (StoreError, InvalidRequest) as exc:
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
            committed_offset = offset
            committed_segment = segment.name
        if report.errors:
            break

    report.rev = expected_rev - 1
    report.last_hash = expected_hash
    report.transactions = len(txns)
    report.committed_offset = committed_offset
    report.segment = committed_segment
    report.ok = not report.errors

    anchor = _latest_anchor(base) if (base / ANCHOR_DIR).is_dir() else None
    if anchor is not None and report.ok:
        key = anchor_key if anchor_key is not None else _anchor_key_from_store(base)
        if key is None:
            report.errors.append("anchor exists but key is unavailable")
        else:
            problem = _verify_anchor(anchor, key)
            if problem:
                report.errors.append(problem)
            else:
                try:
                    anchor_rev = int(anchor["rev"])
                    anchor_hash = str(anchor["hash"])
                    report.anchor_rev = anchor_rev
                    if report.rev < anchor_rev:
                        report.errors.append(
                            "journal truncated below anchor"
                        )
                    elif anchor_rev > 0 and txns[anchor_rev - 1]["hash"] != anchor_hash:
                        report.errors.append("anchor hash does not match journal")
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    report.errors.append(f"bad anchor rev/hash: {exc}")
        report.ok = not report.errors

    if report.ok:
        snapshots = sorted((base / SNAP_DIR).glob("*.json"))
        if snapshots:
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

    return ScanResult(report, state, txns, requests, events, anchor)


class Store:
    """Single-writer TEXTSTORE with group commit, CAS, anchors and watch."""

    def __init__(
        self,
        path: str | Path,
        segment_size: int = DEFAULT_SEGMENT_SIZE,
        commit_window: float = DEFAULT_COMMIT_WINDOW,
        watch_buffer: int = DEFAULT_WATCH_BUFFER,
        anchor_key: bytes | str | None = None,
    ) -> None:
        if not 0.002 <= float(commit_window) <= 0.010:
            raise ValueError("commit_window must be in [0.002, 0.010] seconds")
        if int(segment_size) <= 0:
            raise ValueError("segment_size must be positive")
        if int(watch_buffer) <= 0:
            raise ValueError("watch_buffer must be positive")
        self.path = Path(path)
        self.segment_size = int(segment_size)
        self.commit_window = float(commit_window)
        self.watch_buffer = int(watch_buffer)
        self.path.mkdir(parents=True, exist_ok=True)
        for name in (LOG_DIR, SNAP_DIR, ANCHOR_DIR, STATE_DIR, VIEWS_DIR):
            (self.path / name).mkdir(parents=True, exist_ok=True)
        self._anchor_key = self._init_anchor_key(anchor_key)

        self._lock_fd = os.open(
            str(self.path / LOCK_NAME), os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lock_fd)
            self._lock_fd = -1
            raise StoreBusy(f"another writer holds {self.path / LOCK_NAME}") from exc

        scan = scan_store(self.path, repair=True, anchor_key=self._anchor_key)
        if not scan.report.ok:
            os.close(self._lock_fd)
            self._lock_fd = -1
            raise StoreCorrupt("; ".join(scan.report.errors))
        self._state = scan.state
        self._txns = scan.txns
        self._events = scan.events
        self._request_cache = scan.request_cache
        self._rev = scan.report.rev
        self._last_hash = scan.report.last_hash
        self._committed_offset = scan.report.committed_offset
        self._segment_name = scan.report.segment
        self._anchor = scan.anchor
        self._last_anchor_rev = int(scan.anchor["rev"]) if scan.anchor else 0
        self._read_only = False
        self._fail_reason: str | None = None
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._pending: deque[_Request] = deque()
        self._stopping = False
        self._closed = False
        self._busy = threading.Event()
        self._busy.set()
        self._subscribers: set[_Subscriber] = set()
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

    def _init_anchor_key(self, supplied: bytes | str | None) -> bytes:
        if supplied is not None:
            return supplied.encode("utf-8") if isinstance(supplied, str) else supplied
        existing = _anchor_key_from_store(self.path)
        if existing:
            return existing
        key = secrets.token_bytes(32)
        _write_atomic(self.path / ANCHOR_KEY, key)
        try:
            os.chmod(self.path / ANCHOR_KEY, 0o600)
        except OSError:
            pass
        return key

    # -- lifecycle -------------------------------------------------------
    def _check_open(self) -> None:
        if self._closed:
            raise StoreError("store is closed")

    @property
    def read_only(self) -> bool:
        return self._read_only

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

    # -- reads / API -----------------------------------------------------
    def get(self, key: str) -> dict[str, Any] | None:
        self._check_open()
        key = _safe_key(key)
        with self._lock:
            value = self._state.get(key)
            return copy.deepcopy(value) if value is not None else None

    def list(
        self,
        prefix: str = "",
        label_selector: dict[str, str] | None = None,
        limit: int | None = None,
        continue_token: str | None = None,
    ):
        self._check_open()
        if prefix and "\x00" in prefix:
            raise InvalidRequest("invalid prefix")
        with self._lock:
            keys = sorted(k for k in self._state if k.startswith(prefix))
            values = [copy.deepcopy(self._state[k]) for k in keys]
        if label_selector:
            filtered = []
            for value in values:
                labels = value.get("labels", {})
                if isinstance(labels, dict) and all(
                    labels.get(k) == v for k, v in label_selector.items()
                ):
                    filtered.append(value)
            values = filtered
        start = 0
        if continue_token:
            try:
                start = int(continue_token)
            except ValueError as exc:
                raise InvalidRequest("invalid continue token") from exc
        if start < 0 or start > len(values):
            raise InvalidRequest("invalid continue token")
        if limit is not None and limit <= 0:
            raise InvalidRequest("limit must be positive")
        end = len(values) if limit is None else start + limit
        page = values[start:end]
        next_token = str(end) if end < len(values) else None
        return page, next_token

    def watch(
        self, from_rev: int = 0, timeout: float | None = None
    ) -> Iterator[dict[str, Any]]:
        self._check_open()
        if not isinstance(from_rev, int) or from_rev < 0:
            raise InvalidRequest("from_rev must be a non-negative int")
        subscriber = self._subscribe(from_rev)

        def iterator() -> Iterator[dict[str, Any]]:
            deadline = None if timeout is None else time.monotonic() + timeout
            try:
                while True:
                    with subscriber.condition:
                        while (
                            not subscriber.queue
                            and not subscriber.gone
                            and not self._closed
                        ):
                            remaining = (
                                None
                                if deadline is None
                                else deadline - time.monotonic()
                            )
                            if remaining is not None and remaining <= 0:
                                return
                            subscriber.condition.wait(
                                timeout=remaining if remaining is not None else 0.05
                            )
                        if subscriber.gone:
                            raise WatchGone(
                                "watch buffer exceeded; relist required",
                                subscriber.last_rev,
                            )
                        if self._closed:
                            return
                        event = subscriber.queue.popleft()
                        subscriber.last_rev = int(event["rev"])
                    yield event
            finally:
                self._unsubscribe(subscriber)

        return iterator()

    def _subscribe(self, from_rev: int) -> "_Subscriber":
        subscriber = _Subscriber(from_rev)
        with self._lock:
            self._subscribers.add(subscriber)
            backlog = [
                event for event in self._events if int(event["rev"]) > from_rev
            ]
            for event in backlog:
                if not self._enqueue(subscriber, event):
                    break
        return subscriber

    def _unsubscribe(self, subscriber: "_Subscriber") -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def _enqueue(self, subscriber: "_Subscriber", event: dict[str, Any]) -> bool:
        key = (int(event["rev"]), int(event.get("seq", 0)))
        with subscriber.condition:
            if key in subscriber.seen:
                return True
            subscriber.seen.add(key)
            if len(subscriber.queue) >= self.watch_buffer:
                subscriber.gone = True
                subscriber.queue.clear()
                subscriber.condition.notify_all()
                return False
            subscriber.queue.append(copy.deepcopy(event))
            subscriber.condition.notify_all()
            return True

    # -- writes ----------------------------------------------------------
    def _submit(self, request: "_Request") -> Any:
        self._check_open()
        if self._read_only:
            raise StoreReadOnly(self._fail_reason or "store is read-only until fsck")
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
        expected_rev: int | str | None = None,
        request_id: str | None = None,
        dry_run: bool = False,
        actor: str = "",
        *,
        expected_rv: int | str | None = None,
    ) -> dict[str, Any]:
        if expected_rv is not None:
            if expected_rev is not None:
                raise InvalidRequest("use expected_rev or expected_rv, not both")
            expected_rev = expected_rv
        key = _safe_key(key)
        if not isinstance(value, dict):
            raise InvalidRequest("value must be an object")
        normalized = _normalize(value)
        if len(_jcs(normalized)) > MAX_VALUE_BYTES:
            raise PayloadTooLarge("value exceeds 1 MiB")
        return self._submit(
            _Request(
                kind="put",
                key=key,
                value=normalized,
                expected_rev=expected_rev,
                request_id=_nfc(request_id) if request_id else None,
                dry_run=dry_run,
                actor=_nfc(actor),
            )
        )

    def delete(
        self,
        key: str,
        expected_rev: int | str | None = None,
        request_id: str | None = None,
        actor: str = "",
        *,
        expected_rv: int | str | None = None,
    ) -> None:
        if expected_rv is not None:
            if expected_rev is not None:
                raise InvalidRequest("use expected_rev or expected_rv, not both")
            expected_rev = expected_rv
        key = _safe_key(key)
        self._submit(
            _Request(
                kind="delete",
                key=key,
                value=None,
                expected_rev=expected_rev,
                request_id=_nfc(request_id) if request_id else None,
                dry_run=False,
                actor=_nfc(actor),
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
        # CAS is evaluated while building the batch, under the writer lock.
        with self._lock:
            if self._read_only:
                for request in batch:
                    request.error = StoreReadOnly(
                        self._fail_reason or "store is read-only until fsck"
                    )
                return
            state = dict(self._state)
            rev = self._rev
            last_hash = self._last_hash
            lines: list[bytes] = []
            new_txns: list[dict[str, Any]] = []
            new_events: list[dict[str, Any]] = []
            changed: set[str] = set()
            cache = dict(self._request_cache)
            accepted: list[_Request] = []
            for request in batch:
                if request.request_id and request.request_id in cache:
                    request.result = copy.deepcopy(cache[request.request_id])
                    continue
                current = state.get(request.key)
                current_rv = (
                    str(current.get("resourceVersion", "")) if current else ""
                )
                if not _rv_matches(request.expected_rev, current_rv, current):
                    request.error = ConflictError(
                        f"expected resourceVersion {request.expected_rev}, "
                        f"current {current_rv or '<missing>'}"
                    )
                    continue
                next_rev = rev + 1
                if request.dry_run:
                    preview = copy.deepcopy(request.value or {})
                    preview["resourceVersion"] = f"{next_rev}-preview"
                    request.result = preview
                    continue
                stored = request.result
                if request.kind == "delete":
                    if current is None:
                        request.result = None
                        continue
                    txn = [{"op": "del", "key": request.key}]
                    event = {
                        "rev": next_rev,
                        "seq": 0,
                        "type": "DELETED",
                        "key": request.key,
                        "resourceVersion": current_rv,
                        "value": None,
                    }
                    stored = None
                else:
                    stored = copy.deepcopy(request.value or {})
                    uid = (
                        str(current.get("uid"))
                        if current and current.get("uid")
                        else str(stored.get("uid") or uuid.uuid4().hex)
                    )
                    generation = int((current or {}).get("generation", 1))
                    if current and current.get("spec") != stored.get("spec"):
                        generation += 1
                    stored["uid"] = uid
                    stored["generation"] = generation
                    stored["resourceVersion"] = f"{next_rev}-{uid[:8]}"
                    txn = [{"op": "put", "key": request.key, "value": stored}]
                    event = {
                        "rev": next_rev,
                        "seq": 0,
                        "type": "MODIFIED" if current else "ADDED",
                        "key": request.key,
                        "resourceVersion": stored["resourceVersion"],
                        "value": copy.deepcopy(stored),
                    }
                record = {
                    "fmt": FORMAT,
                    "rev": next_rev,
                    "prev": last_hash,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "actor": request.actor,
                    "requestId": request.request_id or "",
                    "txn": txn,
                }
                record["hash"] = _record_hash(record)
                line = _jcs(record) + b"\n"
                if len(line) > MAX_LINE_BYTES:
                    request.error = PayloadTooLarge("journal line exceeds 2 MiB")
                    continue
                if request.kind == "delete":
                    state.pop(request.key, None)
                    request.result = None
                else:
                    state[request.key] = stored
                    request.result = stored
                if request.request_id:
                    cache[request.request_id] = copy.deepcopy(request.result)
                lines.append(line)
                new_txns.append(record)
                new_events.append(event)
                changed.add(request.key)
                accepted.append(request)
                rev = next_rev
                last_hash = record["hash"]

        if not lines:
            return
        segment = self._current_segment()
        if (
            segment.exists()
            and segment.stat().st_size > 0
            and segment.stat().st_size + sum(len(line) for line in lines)
            > self.segment_size
        ):
            segment = self._next_segment()
        original_size = segment.stat().st_size if segment.exists() else 0
        batch_start = original_size
        total_bytes = sum(len(line) for line in lines)
        try:
            self._append_lines(segment, lines)
        except OSError as exc:
            self._fail_stop(segment, batch_start, exc)
            for request in accepted:
                request.error = DurabilityError(
                    f"write/fsync failed: {exc}"
                )
            return

        with self._lock:
            self._state = state
            self._rev = rev
            self._last_hash = last_hash
            self._txns.extend(new_txns)
            self._events.extend(new_events)
            self._request_cache = cache
            self._committed_offset = batch_start + total_bytes
            self._segment_name = segment.name
            self._stats["fsyncs"] += 1
            self._maybe_anchor(rev, last_hash)
            self._write_meta()
            for subscriber in list(self._subscribers):
                for event in new_events:
                    if not self._enqueue(subscriber, event):
                        break
            self._materialize(changed, state)
        try:
            self._after_fsync()
        except BaseException as exc:
            for request in accepted:
                if request.error is None and request.result is not None:
                    request.error = (
                        exc if isinstance(exc, Exception) else StoreError(str(exc))
                    )
            return
        for request in accepted:
            request.result = copy.deepcopy(request.result)

    def _fail_stop(self, segment: Path, batch_start: int, exc: OSError) -> None:
        try:
            with segment.open("r+b") as handle:
                handle.truncate(batch_start)
                handle.flush()
                self._fsync_fd(handle.fileno())
        except OSError:
            pass
        self._read_only = True
        self._fail_reason = f"fail-stop after {exc}; run nqctl store fsck --repair"

    def _append_lines(self, segment: Path, lines: list[bytes]) -> None:
        with segment.open("ab") as handle:
            for line in lines:
                handle.write(line)
            handle.flush()
            self._fsync_fd(handle.fileno())

    def _after_fsync(self) -> None:
        """Test seam: crash after durability, before client response."""

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

    def _maybe_anchor(self, rev: int, last_hash: str) -> None:
        if rev < ANCHOR_INTERVAL:
            return
        if rev // ANCHOR_INTERVAL <= self._last_anchor_rev // ANCHOR_INTERVAL:
            return
        try:
            self.anchor()
        except OSError as exc:
            self._read_only = True
            self._fail_reason = f"anchor write failed: {exc}"

    def anchor(self) -> int:
        self._check_open()
        with self._lock:
            rev = self._rev
            last_hash = self._last_hash
        body = {"fmt": FORMAT, "rev": rev, "hash": last_hash}
        signature = "hmac-sha256:" + hmac.new(
            self._anchor_key, _jcs(body), hashlib.sha256
        ).hexdigest()
        payload = {**body, "sig": signature}
        _write_atomic(
            self.path / ANCHOR_DIR / f"{rev:010d}.anchor",
            _jcs(payload) + b"\n",
        )
        with self._lock:
            self._last_anchor_rev = rev
            self._anchor = payload
        self._write_meta()
        return rev

    def _write_meta(self) -> None:
        with self._lock:
            anchor = (
                {"rev": int(self._anchor["rev"]), "hash": str(self._anchor["hash"])}
                if self._anchor
                else None
            )
            payload = {
                "fmt": FORMAT,
                "rev": self._rev,
                "last_hash": self._last_hash,
                "committedOffset": self._committed_offset,
                "segment": self._segment_name,
                "anchor": anchor,
            }
        _write_atomic(self.path / META_NAME, _jcs(payload) + b"\n")

    def _materialize(
        self, changed: Iterable[str], state: dict[str, dict[str, Any]]
    ) -> None:
        for key in changed:
            path = _state_path(self.path, key)
            if key in state:
                _write_atomic(path, _jcs(state[key]) + b"\n")
            elif path.exists():
                path.unlink()

    def _materialize_all(self) -> None:
        for key, value in self._state.items():
            _write_atomic(_state_path(self.path, key), _jcs(value) + b"\n")

    def _write_views(self) -> None:
        views = self.path / VIEWS_DIR
        index_lines = ["# Nemeq state", "", "| key | resourceVersion |", "|---|---|"]
        for key in sorted(self._state):
            index_lines.append(
                f"| {key} | {self._state[key].get('resourceVersion', '')} |"
            )
        _write_atomic(
            views / "index.md", ("\n".join(index_lines) + "\n").encode("utf-8")
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
        payload = {"fmt": FORMAT, "rev": rev, "hash": last_hash, "state": state}
        _write_atomic(
            self.path / SNAP_DIR / f"{rev:010d}.json", _jcs(payload) + b"\n"
        )
        snapshots = sorted((self.path / SNAP_DIR).glob("*.json"))
        for old in snapshots[:-2]:
            old.unlink()
        self._write_views()
        return rev

    def fsck(self, repair: bool = False) -> FsckReport:
        report = scan_store(
            self.path, repair=repair, anchor_key=self._anchor_key
        ).report
        if report.ok and self._read_only:
            self._read_only = False
            self._fail_reason = None
            self._committed_offset = report.committed_offset
            self._segment_name = report.segment
            self._write_meta()
        return report

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
    expected_rev: int | str | None
    request_id: str | None
    dry_run: bool
    actor: str
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class _Subscriber:
    def __init__(self, from_rev: int) -> None:
        self.from_rev = from_rev
        self.last_rev = from_rev
        self.queue: deque[dict[str, Any]] = deque()
        self.seen: set[tuple[int, int]] = set()
        self.gone = False
        self.condition = threading.Condition()


def _rv_matches(
    expected: int | str | None,
    current_rv: str,
    current: dict[str, Any] | None,
) -> bool:
    if expected is None:
        return True
    if current is None:
        return expected in (0, "0", "")
    if isinstance(expected, int):
        return current_rv.split("-", 1)[0] == str(expected)
    return current_rv == expected


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
