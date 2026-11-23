"""Instance layout for Nemeq massaru (SDD v0.4 §4, 02b).

All functions are pure path helpers.  They never create or mutate anything;
``install`` will create the layout in a later PR.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_STATE_ROOT = Path("/var/nemeq")

# Directories from SDD v0.4 §4 / 02b.
STANDARD_DIRS = (
    "daily",
    "weekly",
    "monthly",
    "paqidutu",
    "zikru-zi",
    "zikru-su",
    "wal",
    "atoms",
    "traces",
    "usage",
    "raw",
    "archive",
    "observability_log",
    "state",
    "view",
)

STANDARD_FILES = (
    "instance.toml",
    "tush.toml",
    "claims.jsonl",
    "settlement.jsonl",
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def safe_epishu_id(epishu_id: str) -> str:
    """Return a path-safe epishu id or raise ValueError.

    v1 ids are UUID strings, but the skeleton accepts a conservative
    alphanumeric subset so a future caller cannot inject ``..`` or ``/``.
    """
    if not isinstance(epishu_id, str) or not _SAFE_ID.fullmatch(epishu_id):
        raise ValueError("invalid epishu_id")
    if epishu_id in {".", ".."}:
        raise ValueError("invalid epishu_id")
    return epishu_id


def resolve_state_root(explicit: str | None = None) -> Path:
    """Resolve the var-zone root.

    Production default is ``/var/nemeq``.  ``NEMEQ_VAR_ROOT`` and the CLI
    ``--state-root`` are test/staging seams; they do not alter the 4-zone
    contract.
    """
    if explicit:
        return Path(explicit).resolve()
    configured = os.environ.get("NEMEQ_VAR_ROOT")
    if configured:
        return Path(configured).resolve()
    return DEFAULT_STATE_ROOT


def epishu_root(state_root: Path) -> Path:
    return state_root / "epishu"


def epishu_dir(state_root: Path, epishu_id: str) -> Path:
    return epishu_root(state_root) / safe_epishu_id(epishu_id)


def instance_file(state_root: Path, epishu_id: str) -> Path:
    return epishu_dir(state_root, epishu_id) / "instance.toml"


def layout_paths(state_root: Path, epishu_id: str) -> dict[str, str]:
    """Return the 02b layout as path strings (no filesystem access)."""
    base = epishu_dir(state_root, epishu_id)
    payload = {"root": str(base)}
    payload.update({name: str(base / name) for name in STANDARD_DIRS})
    payload.update({name: str(base / name) for name in STANDARD_FILES})
    return payload
