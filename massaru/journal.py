"""Append-only event journal for the Nemeq apiserver (D-11, APISERVER v0.2).

No SQL, no external store.  A single writer appends hash-chained JSONL
records to 16 MiB segments; a record is acknowledged only after fsync.
Reads verify the chain; a torn tail is allowed only in the last segment and
only when it has no newline.  Snapshots are atomic JSON files next to the
segments and are used by ``restore`` to replay the tail.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

GENESIS_HASH = "sha256:" + "0" * 64
DEFAULT_SEGMENT_SIZE = 16 * 1024 * 1024
WAL_DIR = "wal"
SNAP_DIR = "snap"
LOCK_NAME = "journal.lock"
VALID_EVENTS = {"ADDED", "MODIFIED", "DELETED"}


class JournalError(Exception):
    """Base journal refusal."""


class CorruptJournal(JournalError):
    """Hash-chain/JSON/segment corruption."""


class ConflictError(JournalError):
    """CAS failure: expected prevRev does not match the tail."""


class DurabilityError(JournalError):
    """The record was not fsynced and therefore is not acknowledged."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash_body(body: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()


@dataclass(frozen=True)
class JournalRecord:
    rev: int
    ts: str
    type: str
    resource: str
    name: str
    uid: str
    prev_rev: int
    actor: str
    request_id: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def body(self) -> dict[str, Any]:
        return {
            "rev": self.rev,
            "ts": self.ts,
            "type": self.type,
            "resource": self.resource,
            "name": self.name,
            "uid": self.uid,
            "prevRev": self.prev_rev,
            "actor": self.actor,
            "requestId": self.request_id,
            "payload": self.payload,
            "prevHash": self.prev_hash,
        }

    def to_line(self) -> bytes:
        payload = self.body()
        payload["hash"] = self.hash
        return _canonical(payload) + b"\n"

    @classmethod
    def from_line(cls, line: bytes) -> "JournalRecord":
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorruptJournal(f"invalid journal line: {exc}") from exc
        if not isinstance(payload, dict):
            raise CorruptJournal("journal line must be an object")
        try:
            record = cls(
                rev=int(payload["rev"]),
                ts=str(payload["ts"]),
                type=str(payload["type"]),
                resource=str(payload["resource"]),
                name=str(payload["name"]),
                uid=str(payload["uid"]),
                prev_rev=int(payload["prevRev"]),
                actor=str(payload["actor"]),
                request_id=str(payload["requestId"]),
                payload=payload["payload"],
                prev_hash=str(payload["prevHash"]),
                hash=str(payload["hash"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptJournal(f"journal record shape: {exc}") from exc
        if not isinstance(record.payload, dict):
            raise CorruptJournal("journal payload must be an object")
        expected = _hash_body(record.body())
        if record.hash != expected:
            raise CorruptJournal(
                f"hash mismatch at rev {record.rev}: "
                f"stored={record.hash} computed={expected}"
            )
        return record


@dataclass(frozen=True)
class RestoreResult:
    rev: int
    hash: str
    state: dict[str, Any]
    replayed: tuple[JournalRecord, ...]
    snapshot_rev: int


class Journal:
    """One-writer journal with segments, hash-chain, snapshots and restore."""

    def __init__(
        self,
        base_dir: str | Path,
        segment_size: int = DEFAULT_SEGMENT_SIZE,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.segment_size = int(segment_size)
        if self.segment_size <= 0:
            raise ValueError("segment_size must be positive")
        self.wal_dir = self.base_dir / WAL_DIR
        self.snap_dir = self.base_dir / SNAP_DIR
        self.lock_path = self.base_dir / LOCK_NAME
        self.wal_dir.mkdir(parents=True, exist_ok=True)
        self.snap_dir.mkdir(parents=True, exist_ok=True)

    # -- segment helpers -------------------------------------------------
    def _segments(self) -> list[Path]:
        return sorted(self.wal_dir.glob("*.log"))

    def _segment_seq(self, path: Path) -> int:
        try:
            return int(path.stem)
        except ValueError as exc:
            raise CorruptJournal(f"bad segment name: {path}") from exc

    def _current_segment(self) -> Path:
        segments = self._segments()
        return segments[-1] if segments else self.wal_dir / "000001.log"

    def _next_segment(self) -> Path:
        segments = self._segments()
        sequence = self._segment_seq(segments[-1]) + 1 if segments else 1
        return self.wal_dir / f"{sequence:06d}.log"

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # -- read/verify -----------------------------------------------------
    def read_all(self, repair: bool = False) -> list[JournalRecord]:
        records: list[JournalRecord] = []
        expected_rev = 1
        expected_prev_hash = GENESIS_HASH
        segments = self._segments()
        for segment_index, path in enumerate(segments):
            is_last = segment_index == len(segments) - 1
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise CorruptJournal(f"cannot read {path}: {exc}") from exc
            offset = 0
            for line in data.splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    if not is_last:
                        raise CorruptJournal(
                            f"torn tail in non-last segment {path}"
                        )
                    if repair:
                        try:
                            with path.open("r+b") as handle:
                                handle.truncate(offset)
                                handle.flush()
                                os.fsync(handle.fileno())
                        except OSError:
                            pass
                    break
                record = JournalRecord.from_line(line[:-1])
                if record.rev != expected_rev:
                    raise CorruptJournal(
                        f"rev gap in {path}: expected {expected_rev}, "
                        f"got {record.rev}"
                    )
                if record.prev_rev != expected_rev - 1:
                    raise CorruptJournal(
                        f"prevRev mismatch at rev {record.rev}"
                    )
                if record.prev_hash != expected_prev_hash:
                    raise CorruptJournal(
                        f"prevHash mismatch at rev {record.rev}"
                    )
                records.append(record)
                expected_rev += 1
                expected_prev_hash = record.hash
                offset += len(line)
        return records

    def tail(self) -> JournalRecord | None:
        records = self.read_all()
        return records[-1] if records else None

    # -- append ----------------------------------------------------------
    def append(
        self,
        event_type: str,
        resource: str,
        name: str,
        uid: str,
        payload: dict[str, Any],
        actor: str = "",
        request_id: str = "",
        expected_rev: int | None = None,
    ) -> JournalRecord:
        if event_type not in VALID_EVENTS:
            raise JournalError(f"invalid event type: {event_type!r}")
        if not isinstance(payload, dict):
            raise JournalError("payload must be an object")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                records = self.read_all(repair=True)
                last = records[-1] if records else None
                last_rev = last.rev if last else 0
                if expected_rev is not None and expected_rev != last_rev:
                    raise ConflictError(
                        f"expected rev {expected_rev}, current {last_rev}"
                    )
                prev_hash = last.hash if last else GENESIS_HASH
                record = JournalRecord(
                    rev=last_rev + 1,
                    ts=datetime.now(timezone.utc).isoformat(),
                    type=event_type,
                    resource=resource,
                    name=name,
                    uid=uid,
                    prev_rev=last_rev,
                    actor=actor,
                    request_id=request_id,
                    payload=payload,
                    prev_hash=prev_hash,
                    hash="",
                )
                body = record.body()
                record = JournalRecord(
                    **{**asdict(record), "hash": _hash_body(body)}
                )
                line = record.to_line()

                segment = self._current_segment()
                if (
                    segment.exists()
                    and segment.stat().st_size + len(line)
                    > self.segment_size
                    and segment.stat().st_size > 0
                ):
                    segment = self._next_segment()
                segment.parent.mkdir(parents=True, exist_ok=True)
                original_size = segment.stat().st_size if segment.exists() else 0
                try:
                    with segment.open("ab") as handle:
                        handle.write(line)
                        handle.flush()
                        os.fsync(handle.fileno())
                except OSError as exc:
                    # The client must not receive an un-fsynced record.
                    try:
                        with segment.open("r+b") as handle:
                            handle.truncate(original_size)
                            handle.flush()
                            os.fsync(handle.fileno())
                    except OSError:
                        pass
                    raise DurabilityError(
                        f"fsync failed for rev {record.rev}: {exc}"
                    ) from exc
                return record
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    # -- snapshots -------------------------------------------------------
    def snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise JournalError("snapshot state must be an object")
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                records = self.read_all(repair=True)
                last = records[-1] if records else None
                rev = last.rev if last else 0
                last_hash = last.hash if last else GENESIS_HASH
                payload = {
                    "rev": rev,
                    "hash": last_hash,
                    "state": state,
                    "ts": time.time(),
                }
                path = self.snap_dir / f"{rev:010d}.snap"
                handle_fd, temp_name = tempfile.mkstemp(
                    prefix=".snap.", dir=str(self.snap_dir)
                )
                try:
                    with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                        json.dump(
                            payload,
                            handle,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_name, path)
                    self._fsync_dir(self.snap_dir)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
                return payload
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def latest_snapshot(self) -> dict[str, Any] | None:
        snapshots = sorted(self.snap_dir.glob("*.snap"))
        if not snapshots:
            return None
        try:
            payload = json.loads(snapshots[-1].read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CorruptJournal(f"cannot read snapshot: {exc}") from exc
        if not isinstance(payload, dict):
            raise CorruptJournal("snapshot must be an object")
        if not isinstance(payload.get("rev"), int):
            raise CorruptJournal("snapshot rev must be int")
        if not isinstance(payload.get("hash"), str):
            raise CorruptJournal("snapshot hash must be string")
        if not isinstance(payload.get("state"), dict):
            raise CorruptJournal("snapshot state must be object")
        return payload

    def restore(
        self,
        reducer: Callable[[dict[str, Any], JournalRecord], dict[str, Any]]
        | None = None,
    ) -> RestoreResult:
        records = self.read_all(repair=True)
        snapshot = self.latest_snapshot()
        snapshot_rev = 0
        state: dict[str, Any] = {}
        last_hash = GENESIS_HASH
        if snapshot is not None:
            snapshot_rev = int(snapshot["rev"])
            state = dict(snapshot["state"])
            last_hash = str(snapshot["hash"])
            if snapshot_rev > len(records):
                raise CorruptJournal("snapshot rev beyond journal tail")
            if snapshot_rev > 0:
                if records[snapshot_rev - 1].hash != last_hash:
                    raise CorruptJournal("snapshot hash does not match journal")
        tail = tuple(record for record in records if record.rev > snapshot_rev)
        for record in tail:
            if reducer is not None:
                state = reducer(state, record)
            last_hash = record.hash
        rev = tail[-1].rev if tail else snapshot_rev
        if rev == 0:
            last_hash = GENESIS_HASH
        return RestoreResult(
            rev=rev,
            hash=last_hash,
            state=state,
            replayed=tail,
            snapshot_rev=snapshot_rev,
        )
