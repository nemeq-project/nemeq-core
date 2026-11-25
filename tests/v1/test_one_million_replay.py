import time
import pytest
from massaru.store import scan_store
from tools.one_million import TOTAL_REVS, build_journal, journal_size_bytes


@pytest.mark.slow
def test_one_million_replay_and_fsck(tmp_path):
    """Полный проход 1M ревизий (генерация -> replay+fsck), порог replay <=120 s."""
    started = time.monotonic()
    build_journal(tmp_path, TOTAL_REVS)
    generated = time.monotonic() - started

    scan_started = time.monotonic()
    result = scan_store(tmp_path)
    replay_seconds = time.monotonic() - scan_started

    assert result.report.ok is True, result.report.errors
    assert result.report.transactions == TOTAL_REVS
    assert len(result.state) == TOTAL_REVS
    assert result.report.last_hash.startswith("sha256:")
    assert replay_seconds <= 120.0, f"replay took {replay_seconds:.1f}s"
    assert journal_size_bytes(tmp_path) > 0
    print(f"1M replay {replay_seconds:.1f}s, seed {generated:.1f}s")
