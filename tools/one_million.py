"""Journal generation helper for the 1M revision acceptance test."""

from __future__ import annotations

from pathlib import Path

from massaru.store import _batch_hash, _commit_hash, _jcs, _record_hash

TOTAL_REVS: int = 1_000_000


def build_journal(root: Path, total: int) -> Path:
    """Create root/log/*.jsonl with total (transaction, commit) pairs."""
    log_dir = root / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "000001.jsonl"
    prev: str = "sha256:" + "0" * 64
    buf: list[bytes] = []
    with path.open("ab") as fh:
        for rev in range(1, total + 1):
            record = {
                "fmt": 1, "rev": rev, "prev": prev,
                "ts": "2026-11-25T00:00:00+00:00", "actor": "bench",
                "requestId": "",
                "txn": [{"op": "put", "key": f"epishus/e{rev}",
                         "value": {"name": f"e{rev}",
                                   "spec": {"ziRef": "z", "suRef": "s"},
                                   "status": {}, "uid": "u", "generation": 1,
                                   "resourceVersion": f"{rev}-u"}}],
            }
            record["hash"] = _record_hash(record)
            commit = {
                "fmt": 1, "type": "commit", "batchId": f"b{rev}",
                "fromRev": rev, "toRev": rev, "count": 1,
                "batchHash": _batch_hash([record]),
                "prev": record["hash"],
            }
            commit["hash"] = _commit_hash(commit)
            buf.append(_jcs(record) + b"\n")
            buf.append(_jcs(commit) + b"\n")
            prev = commit["hash"]
            if len(buf) >= 2000:
                fh.write(b"".join(buf))
                buf.clear()
        if buf:
            fh.write(b"".join(buf))
    return root


def journal_size_bytes(root: Path) -> int:
    """Total size of root/log/*.jsonl in bytes."""
    return sum(path.stat().st_size for path in (root / "log").glob("*.jsonl"))
