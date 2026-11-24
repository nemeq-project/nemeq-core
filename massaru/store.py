"""Read-only instance lookup for the massaru skeleton."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .layout import epishu_dir, epishu_root, instance_file, safe_epishu_id


def _read_instance(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def list_instances(state_root: Path) -> list[dict[str, Any]]:
    """List installed epishu directories that contain instance.toml.

    Missing state root is an empty list, not an error: list is a read-only
    observation and v1 must not create directories while reading.
    """
    root = epishu_root(state_root)
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        try:
            safe_epishu_id(child.name)
        except ValueError:
            continue
        instance = _read_instance(child / "instance.toml")
        if instance is None:
            continue
        result.append(
            {
                "epishu_id": child.name,
                "state": str(instance.get("state", "unknown")),
                "path": str(child),
            }
        )
    return result


def inspect_instance(
    state_root: Path, epishu_id: str
) -> dict[str, Any] | None:
    path = epishu_dir(state_root, epishu_id)
    instance = _read_instance(instance_file(state_root, epishu_id))
    if instance is None:
        return None
    return {
        "epishu_id": epishu_id,
        "path": str(path),
        "instance": instance,
    }
