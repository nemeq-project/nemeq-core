"""Tests for the LMDB-backed Store (APISERVER v0.3, D-11)."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import lmdb
import pytest

from massaru.store import (
    ConflictError,
    GENESIS_HASH,
    Store,
    StoreCorrupt,
    StoreError,
    _change_hash,
)


def epishu(s: Store, name: str, value: int = 1, expected_rev=None):
    return s.put(
        "Epishu",
        name,
        {"spec": {"value": value}, "status": {"phase": "Pending"}},
        expected_rev=expected_rev,
    )


def test_put_get_list_delete(tmp_path: Path):
    with Store(tmp_path / "db") as s:
        first = epishu(s, "e1")
        epishu(s, "e2")
        s.put("Su", "s1", {"spec": {"image": "su"}})

        assert first["name"] == "e1"
        assert first["uid"]
        assert first["resourceVersion"] == 1
        assert s.get("Epishu", "e1")["spec"] == {"value": 1}
        assert s.get("Epishu", "missing") is None
        assert [o["name"] for o in s.list("Epishu")] == ["e1", "e2"]
        assert len(s.list()) == 3

        assert s.delete("Epishu", "e1", expected_rev=1) is True
        assert s.get("Epishu", "e1") is None
        assert s.delete("Epishu", "e1") is False
        assert s.verify() is True


def test_cas_conflict_on_put_and_delete(tmp_path: Path):
    with Store(tmp_path / "db") as s:
        epishu(s, "e1", expected_rev=0)
        with pytest.raises(ConflictError):
            epishu(s, "e1", value=2, expected_rev=0)
        assert s.get("Epishu", "e1")["resourceVersion"] == 1

        updated = epishu(s, "e1", value=2, expected_rev=1)
        assert updated["resourceVersion"] == 2
        with pytest.raises(ConflictError):
            s.delete("Epishu", "e1", expected_rev=1)
        assert s.delete("Epishu", "e1", expected_rev=2) is True


def test_restart_preserves_objects_and_rev(tmp_path: Path):
    path = tmp_path / "db"
    s = Store(path)
    epishu(s, "e1")
    epishu(s, "e2")
    s.close()

    s2 = Store(path)
    try:
        assert s2.get("Epishu", "e1")["resourceVersion"] == 1
        assert s2.get("Epishu", "e2")["resourceVersion"] == 2
        third = epishu(s2, "e3", expected_rev=0)
        assert third["resourceVersion"] == 3
        assert s2.verify() is True
        changes = list(s2.watch(0, timeout=0.01))
        assert [c["rev"] for c in changes] == [1, 2, 3]
    finally:
        s2.close()


def test_corrupt_hash_chain_raises_on_open(tmp_path: Path):
    path = tmp_path / "db"
    s = Store(path)
    epishu(s, "e1")
    epishu(s, "e2")
    s.close()

    env = lmdb.open(str(path), map_size=64 * 1024 * 1024)
    try:
        key = b"chg\x00" + f"{1:020d}".encode("ascii")
        with env.begin(write=True) as txn:
            raw = txn.get(key)
            assert raw is not None
            change = json.loads(raw)
            change["prevHash"] = "sha256:" + "f" * 64
            change["hash"] = _change_hash(change)
            txn.put(key, json.dumps(change, sort_keys=True).encode("utf-8"))
    finally:
        env.close()

    with pytest.raises(StoreCorrupt):
        Store(path)


def test_restore_from_copy(tmp_path: Path):
    path = tmp_path / "db"
    backup = tmp_path / "backup"
    s = Store(path)
    epishu(s, "e1")
    epishu(s, "e2")
    s.backup(backup)
    s.close()

    restored = Store(backup)
    try:
        assert restored.get("Epishu", "e1")["spec"] == {"value": 1}
        assert len(restored.list("Epishu")) == 2
        third = epishu(restored, "e3", expected_rev=0)
        assert third["resourceVersion"] == 3
        assert restored.verify() is True
    finally:
        restored.close()


def test_group_commit_batches(tmp_path: Path):
    count = 30
    barrier = threading.Barrier(count)
    with Store(tmp_path / "db", commit_window=0.010) as s:

        def writer(index: int):
            barrier.wait()
            return epishu(s, f"e{index}", value=index, expected_rev=0)

        with ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(writer, range(count)))

        stats = s.stats()
        assert len(results) == count
        assert len(s.list("Epishu")) == count
        assert stats["ops"] == count
        assert stats["max_batch"] > 1
        assert stats["commits"] < stats["ops"]
        assert s.verify() is True


def test_watch_from_rev(tmp_path: Path):
    with Store(tmp_path / "db") as s:
        epishu(s, "e1")
        epishu(s, "e2")
        changes = list(s.watch(0, timeout=0.01))
        assert [c["rev"] for c in changes] == [1, 2]
        assert changes[0]["type"] == "ADDED"
        assert changes[0]["prevHash"] == GENESIS_HASH
        assert list(s.watch(2, timeout=0.01)) == []


def test_delete_writes_deleted_change(tmp_path: Path):
    with Store(tmp_path / "db") as s:
        epishu(s, "e1")
        assert s.delete("Epishu", "e1") is True
        assert s.delete("Epishu", "e1") is False
        changes = list(s.watch(0, timeout=0.01))
        assert [c["type"] for c in changes] == ["ADDED", "DELETED"]
        assert changes[-1]["rev"] == 2
        assert changes[-1]["prevHash"] == changes[0]["hash"]
        assert s.verify() is True


def test_commit_window_is_bounded(tmp_path: Path):
    for invalid in (0.001, 0.02):
        with pytest.raises(ValueError):
            Store(tmp_path / f"db-{invalid}", commit_window=invalid)


def test_no_sync_false_and_public_interface():
    source = (Path(__file__).resolve().parents[2] / "massaru" / "store.py").read_text(
        encoding="utf-8"
    )
    assert "sync=False" not in source
    assert "sync=True" in source
    for method in ("get", "list", "watch", "put", "delete"):
        assert callable(getattr(Store, method))
    assert issubclass(ConflictError, StoreError)
    assert issubclass(StoreCorrupt, StoreError)
