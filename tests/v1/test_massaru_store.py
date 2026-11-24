"""TEXTSTORE v0.1 tests: model, CAS, durability, fsck, restore, group commit."""

from __future__ import annotations

import json
import random
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from massaru import store_fsck
from massaru.store import (
    GENESIS_HASH,
    ConflictError,
    DurabilityError,
    Store,
    StoreBusy,
    StoreCorrupt,
    _txn_hash,
    scan_store,
)


def put(store: Store, key: str, number: int = 1, expected_rev=None, **kwargs):
    return store.put(
        key,
        {"spec": {"n": number}, "status": {}},
        expected_rev=expected_rev,
        **kwargs,
    )


def test_put_get_list_delete_and_materialization(tmp_path: Path):
    path = tmp_path / "store"
    with Store(path) as store:
        first = put(store, "epishu/e1", 1, expected_rev=0)
        put(store, "epishu/e2", 2, expected_rev=0)
        put(store, "su/s1", 3, expected_rev=0)

        assert first["resourceVersion"] == 1
        assert first["uid"]
        assert store.get("epishu/e1")["spec"] == {"n": 1}
        assert store.get("epishu/missing") is None
        assert [v["resourceVersion"] for v in store.list("epishu/")] == [1, 2]
        assert len(store.list()) == 3

        assert store.delete("epishu/e1", expected_rev=1) is None
        assert store.get("epishu/e1") is None
        store.delete("epishu/e1")
        assert store.verify() is True

        assert (path / "state" / "epishu" / "e1.json").exists() is False
        assert (path / "state" / "epishu" / "e2.json").is_file()
        assert (path / "views" / "index.md").is_file()
        assert (path / "views" / "audit.csv").is_file()
        assert (path / "meta.json").is_file()


def test_cas_conflict(tmp_path: Path):
    with Store(tmp_path / "store") as store:
        put(store, "epishu/e1", 1, expected_rev=0)
        with pytest.raises(ConflictError):
            put(store, "epishu/e1", 2, expected_rev=0)
        assert store.get("epishu/e1")["resourceVersion"] == 1
        put(store, "epishu/e1", 2, expected_rev=1)
        with pytest.raises(ConflictError):
            store.delete("epishu/e1", expected_rev=1)
        store.delete("epishu/e1", expected_rev=2)
        assert store.get("epishu/e1") is None


def test_restart_and_meta(tmp_path: Path):
    path = tmp_path / "store"
    store = Store(path)
    put(store, "epishu/e1", 1, expected_rev=0)
    put(store, "epishu/e1", 2, expected_rev=1)
    store.close()

    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    assert meta["rev"] == 2
    assert meta["last_hash"] != GENESIS_HASH

    with Store(path) as reopened:
        assert reopened.get("epishu/e1")["spec"] == {"n": 2}
        third = put(reopened, "epishu/e2", 3, expected_rev=0)
        assert third["resourceVersion"] == 3
        report = reopened.fsck()
        assert report.ok is True
        assert report.rev == 3


def test_two_writers_are_exclusive(tmp_path: Path):
    path = tmp_path / "store"
    first = Store(path)
    try:
        with pytest.raises(StoreBusy):
            Store(path)
    finally:
        first.close()
    with Store(path):
        assert True


def test_fsync_is_called_before_ack(tmp_path: Path):
    store = Store(tmp_path / "store")
    called: list[int] = []
    original = store._fsync_fd

    def counting(fd: int) -> None:
        called.append(fd)
        original(fd)

    store._fsync_fd = counting
    put(store, "epishu/e1", 1, expected_rev=0)
    assert called
    assert store.stats()["fsyncs"] == 1
    store.close()


def test_crash_after_write_before_fsync_is_not_acked(tmp_path: Path):
    path = tmp_path / "store"
    store = Store(path, commit_window=0.002)
    put(store, "epishu/e1", 1, expected_rev=0)

    def broken_fsync(fd: int) -> None:
        raise OSError("simulated crash before fsync")

    store._fsync_fd = broken_fsync
    with pytest.raises(DurabilityError):
        put(store, "epishu/e2", 2, expected_rev=0)
    store.close()

    with Store(path) as reopened:
        assert reopened.get("epishu/e2") is None
        assert reopened.fsck().rev == 1


def test_crash_after_fsync_before_ack_is_durable(tmp_path: Path):
    path = tmp_path / "store"
    store = Store(path, commit_window=0.002)
    put(store, "epishu/e1", 1, expected_rev=0)

    def crash() -> None:
        raise RuntimeError("simulated crash after fsync")

    store._after_fsync = crash
    with pytest.raises(RuntimeError):
        put(store, "epishu/e2", 2, expected_rev=0)
    store.close()

    with Store(path) as reopened:
        assert reopened.get("epishu/e2")["spec"] == {"n": 2}
        report = reopened.fsck()
        assert report.ok is True
        assert report.rev == 2


def test_torn_tail_is_repaired_at_start(tmp_path: Path):
    path = tmp_path / "store"
    store = Store(path)
    put(store, "epishu/e1", 1, expected_rev=0)
    put(store, "epishu/e2", 2, expected_rev=0)
    store.close()

    segment = sorted((path / "log").glob("*.jsonl"))[-1]
    with segment.open("ab") as handle:
        handle.write(b'{"rev":3')
    report = store_fsck.fsck(path, repair=False)
    assert report.ok is False
    assert any("torn" in error for error in report.errors)

    with Store(path) as repaired:
        assert repaired.fsck().rev == 2
        assert repaired.get("epishu/e2")["spec"] == {"n": 2}
    assert b'{"rev":3' not in segment.read_bytes()


def test_corrupt_middle_fails_fsck_and_start(tmp_path: Path):
    path = tmp_path / "store"
    store = Store(path)
    put(store, "epishu/e1", 1, expected_rev=0)
    put(store, "epishu/e2", 2, expected_rev=0)
    put(store, "epishu/e3", 3, expected_rev=0)
    store.close()

    segment = sorted((path / "log").glob("*.jsonl"))[-1]
    lines = segment.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["txn"][0]["value"]["spec"]["n"] = 999
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = store_fsck.fsck(path)
    assert report.ok is False
    assert any("hash mismatch" in error for error in report.errors)
    with pytest.raises(StoreCorrupt):
        Store(path)



def _rewrite_record(segment: Path, index: int, mutate) -> None:
    lines = segment.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[index])
    mutate(record)
    record["hash"] = _txn_hash(
        int(record["rev"]), str(record["prev"]), record["txn"]
    )
    lines[index] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_corrupt_prev_chain_fails(tmp_path: Path):
    path = tmp_path / "store"
    store = Store(path)
    put(store, "epishu/e1", 1, expected_rev=0)
    put(store, "epishu/e2", 2, expected_rev=0)
    store.close()
    segment = sorted((path / "log").glob("*.jsonl"))[-1]
    _rewrite_record(segment, 1, lambda record: record.__setitem__("prev", GENESIS_HASH))
    report = store_fsck.fsck(path)
    assert report.ok is False
    assert any("prev mismatch" in error for error in report.errors)
    with pytest.raises(StoreCorrupt):
        Store(path)


def test_duplicate_rev_fails(tmp_path: Path):
    path = tmp_path / "store"
    store = Store(path)
    put(store, "epishu/e1", 1, expected_rev=0)
    put(store, "epishu/e2", 2, expected_rev=0)
    store.close()
    segment = sorted((path / "log").glob("*.jsonl"))[-1]
    _rewrite_record(segment, 1, lambda record: record.__setitem__("rev", 1))
    report = store_fsck.fsck(path)
    assert report.ok is False
    assert any("rev mismatch" in error for error in report.errors)
    with pytest.raises(StoreCorrupt):
        Store(path)


def test_property_model_vs_store(tmp_path: Path):
    rng = random.Random(20261124)
    path = tmp_path / "store"
    model: dict[str, dict] = {}
    keys = [f"epishu/e{i}" for i in range(8)]
    with Store(path, commit_window=0.002) as store:
        for _ in range(300):
            key = rng.choice(keys)
            current = model.get(key)
            expected = current["resourceVersion"] if current else 0
            conflict = rng.random() < 0.10
            if conflict:
                expected += 1
            if rng.random() < 0.65:
                try:
                    value = store.put(
                        key,
                        {"spec": {"n": rng.randrange(100)}},
                        expected_rev=expected,
                    )
                except ConflictError:
                    assert expected != (current["resourceVersion"] if current else 0)
                    continue
                model[key] = value
            else:
                try:
                    store.delete(key, expected_rev=expected)
                except ConflictError:
                    assert expected != (current["resourceVersion"] if current else 0)
                    continue
                model.pop(key, None)

            assert store.get(key) == model.get(key)
            assert len(store.list()) == len(model)

        assert store.fsck().ok is True
        assert store.verify() is True


def test_group_commit_batches_with_one_fsync(tmp_path: Path):
    count = 30
    barrier = threading.Barrier(count)
    with Store(tmp_path / "store", commit_window=0.010) as store:

        def writer(index: int):
            barrier.wait()
            return store.put(f"epishu/e{index}", {"spec": {"n": index}})

        with ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(writer, range(count)))

        stats = store.stats()
        assert len(results) == count
        assert stats["ops"] == count
        assert stats["max_batch"] > 1
        assert stats["fsyncs"] < count
        assert len(store.list("epishu/")) == count


def test_watch_and_request_idempotency(tmp_path: Path):
    with Store(tmp_path / "store") as store:
        first = put(store, "epishu/e1", 1, expected_rev=0, request_id="req-1")
        duplicate = put(store, "epishu/e1", 2, expected_rev=0, request_id="req-1")
        assert duplicate == first
        assert store.fsck().transactions == 1

        put(store, "epishu/e2", 2, expected_rev=0)
        events = list(store.watch(0, timeout=0.01))
        assert [event["rev"] for event in events] == [1, 2]
        assert events[0]["txn"][0]["op"] == "put"
        assert list(store.watch(2, timeout=0.01)) == []

        store.delete("epishu/e1")
        event = list(store.watch(2, timeout=0.01))[-1]
        assert event["rev"] == 3
        assert event["txn"][0]["op"] == "del"


def test_snapshot_and_restore_from_copy(tmp_path: Path):
    path = tmp_path / "store"
    backup = tmp_path / "backup"
    store = Store(path)
    put(store, "epishu/e1", 1, expected_rev=0)
    put(store, "epishu/e2", 2, expected_rev=0)
    rev = store.snapshot()
    assert rev == 2
    assert (path / "snap" / "0000000002.json").is_file()
    assert (path / "views" / "index.md").is_file()
    store.close()

    shutil.copytree(path, backup)
    with Store(backup) as restored:
        assert restored.get("epishu/e1")["spec"] == {"n": 1}
        assert restored.fsck().rev == 2
        assert restored.snapshot() == 2


def test_commit_window_bounds(tmp_path: Path):
    for value in (0.001, 0.02):
        with pytest.raises(ValueError):
            Store(tmp_path / f"store-{value}", commit_window=value)


def test_no_lmdb_dependency():
    source = (Path(__file__).resolve().parents[2] / "massaru" / "store.py").read_text(
        encoding="utf-8"
    )
    assert "lmdb" not in source.lower()
    for method in ("get", "list", "put", "delete", "watch", "snapshot", "fsck"):
        assert callable(getattr(Store, method))
