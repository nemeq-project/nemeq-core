"""nqctl store fsck|verify|snapshot acceptance."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from massaru.store import Store

REPO = Path(__file__).resolve().parents[2]


def run(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "nqctl", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def make_store(path: Path, sink: Path | None = None) -> None:
    with Store(path, anchor_sink=sink) as store:
        store.put("epishu/e1", {"spec": {"n": 1}}, expected_rev=0)
        store.put("epishu/e2", {"spec": {"n": 2}}, expected_rev=0)
        store.snapshot()
        store.anchor()


def test_nqctl_verify_and_snapshot(tmp_path: Path):
    path = tmp_path / "store"
    make_store(path)

    result = run("--store", str(path), "store", "verify")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["rev"] == 2

    result = run("--store", str(path), "store", "snapshot")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["snapshotRev"] == 2


def test_nqctl_fsck_repairs_torn_tail(tmp_path: Path):
    path = tmp_path / "store"
    make_store(path)
    segment = sorted((path / "log").glob("*.jsonl"))[-1]
    with segment.open("ab") as handle:
        handle.write(b'{"rev":3')

    result = run("--store", str(path), "store", "fsck")
    assert result.returncode == 1
    assert "torn" in result.stdout

    result = run("--store", str(path), "store", "fsck", "--repair")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["repaired"] is True
    assert report["rev"] == 2

    result = run("--store", str(path), "store", "verify")
    assert result.returncode == 0


def test_nqctl_fsck_refuses_corrupt_middle(tmp_path: Path):
    path = tmp_path / "store"
    make_store(path)
    segment = sorted((path / "log").glob("*.jsonl"))[-1]
    lines = segment.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["txn"][0]["value"]["spec"]["n"] = 999
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = run("--store", str(path), "store", "fsck", "--repair")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert any("hash mismatch" in error for error in report["errors"])


def test_nqctl_missing_store_is_empty_ok(tmp_path: Path):
    result = run("--store", str(tmp_path / "missing"), "store", "verify")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["rev"] == 0


def test_nqctl_fsck_remote_watermark(tmp_path: Path):
    path = tmp_path / "store"
    sink = tmp_path / "sink"
    make_store(path, sink=sink)
    result = run(
        "--store", str(path), "store", "verify", "--remote", str(sink)
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["anchorRev"] == 2
