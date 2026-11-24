"""Soak tool must refuse tmpfs and require --store."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "tools/soak.py", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_soak_requires_store():
    result = run("--profile", "A", "--duration", "1")
    assert result.returncode == 2


def test_soak_rejects_tmpfs(tmp_path: Path):
    result = run(
        "--profile", "A", "--duration", "1", "--store", "/tmp/soak-forbidden"
    )
    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["fsType"] == "tmpfs"
    assert "ext4" in payload["error"]
