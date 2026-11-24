"""Append-only journal tests: hash-chain, CAS, durability, corruption, restore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from massaru import journal
from massaru.journal import (
    GENESIS_HASH,
    ConflictError,
    CorruptJournal,
    DurabilityError,
    Journal,
)


def add(j: Journal, n: int, *, expected_rev: int | None = None):
    return j.append(
        "MODIFIED",
        "epishu",
        f"e{n}",
        f"uid-{n}",
        {"n": n},
        expected_rev=expected_rev,
    )


def test_append_read_hash_chain(tmp_path: Path):
    j = Journal(tmp_path / "apiserver")
    first = add(j, 1)
    second = add(j, 2)

    assert first.rev == 1
    assert first.prev_rev == 0
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_rev == 1
    assert second.prev_hash == first.hash
    assert second.rev == 2

    records = j.read_all()
    assert [r.rev for r in records] == [1, 2]
    assert records[0].hash != records[1].hash
    assert j.tail() == second


def test_cas_prev_rev_conflict(tmp_path: Path):
    j = Journal(tmp_path / "apiserver")
    add(j, 1)
    with pytest.raises(ConflictError):
        add(j, 2, expected_rev=0)
    assert j.tail().rev == 1
    accepted = add(j, 2, expected_rev=1)
    assert accepted.rev == 2


def test_fsync_failure_is_not_acknowledged(tmp_path: Path, monkeypatch):
    j = Journal(tmp_path / "apiserver")
    add(j, 1)

    def broken_fsync(fd: int) -> None:
        raise OSError("simulated crash before fsync")

    monkeypatch.setattr(journal.os, "fsync", broken_fsync)
    with pytest.raises(DurabilityError):
        add(j, 2)

    records = j.read_all()
    assert [r.rev for r in records] == [1]
    assert j.restore().rev == 1


def test_torn_tail_is_repaired_only_in_last_segment(tmp_path: Path):
    j = Journal(tmp_path / "apiserver")
    add(j, 1)
    add(j, 2)
    segment = j._segments()[-1]
    with segment.open("ab") as handle:
        handle.write(b'{"rev":3')

    assert [r.rev for r in j.read_all()] == [1, 2]

    # append must repair the torn tail before writing a new record.
    third = add(j, 3)
    assert third.rev == 3
    assert segment.read_bytes().endswith(b"}\n")
    assert [r.rev for r in j.read_all()] == [1, 2, 3]
    assert j.restore().rev == 3


def test_bit_flip_raises_corrupt(tmp_path: Path):
    j = Journal(tmp_path / "apiserver")
    add(j, 1)
    add(j, 2)
    segment = j._segments()[-1]
    data = segment.read_bytes()
    # Keep the JSON valid and change only a value: only hash-chain can catch it.
    assert b'"uid-1"' in data
    segment.write_bytes(data.replace(b'"uid-1"', b'"uid-2"', 1))
    with pytest.raises(CorruptJournal):
        j.read_all()


def test_snapshot_restore_replays_tail(tmp_path: Path):
    j = Journal(tmp_path / "apiserver")
    add(j, 1)
    add(j, 2)
    add(j, 3)
    snapshot = j.snapshot({"count": 3})
    assert snapshot["rev"] == 3
    add(j, 4)
    add(j, 5)

    def reducer(state, record):
        out = dict(state)
        out["count"] += record.payload["n"]
        return out

    result = j.restore(reducer)
    assert result.snapshot_rev == 3
    assert result.rev == 5
    assert result.replayed == tuple(j.read_all()[3:])
    assert result.state == {"count": 12}
    assert result.hash == j.tail().hash


def test_restore_without_snapshot(tmp_path: Path):
    j = Journal(tmp_path / "apiserver")
    add(j, 1)
    add(j, 2)
    result = j.restore()
    assert result.snapshot_rev == 0
    assert result.rev == 2
    assert len(result.replayed) == 2
    assert result.state == {}


def test_segment_rotation_keeps_chain(tmp_path: Path):
    j = Journal(tmp_path / "apiserver", segment_size=200)
    for number in range(1, 8):
        add(j, number)
    assert len(j._segments()) >= 2
    assert [r.rev for r in j.read_all()] == list(range(1, 8))
    result = j.restore()
    assert result.rev == 7
    assert len(result.replayed) == 7


def test_snapshot_is_valid_json(tmp_path: Path):
    j = Journal(tmp_path / "apiserver")
    add(j, 1)
    j.snapshot({"state": "ok"})
    snapshot = j.latest_snapshot()
    assert snapshot is not None
    assert snapshot["rev"] == 1
    assert snapshot["state"] == {"state": "ok"}
    path = j.snap_dir / "0000000001.snap"
    assert json.loads(path.read_text(encoding="utf-8"))["rev"] == 1
