"""Shared pytest markers for the v1 acceptance suite."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "slow: long acceptance runs (1M revisions, measured replay/fsck)",
    )
