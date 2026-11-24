"""zi/su image manifests and deterministic tree digests.

Image layout (v1, SDD v0.4 §3):
    manifest.json   kind/name/version/digest
    ...             image content (anima.toml, body.toml, skills/, ...)

The digest covers every regular file except the root ``manifest.json``.
Entries are sorted by POSIX relative path, so the digest is stable across
filesystems.  Symlinks are rejected: v1 images are read-only trees and a
symlink/hardlink escape must not be accepted during install.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
DIGEST_PREFIX = "sha256:"


class ImageError(ValueError):
    """Malformed image, manifest or unsafe tree entry."""


class DigestMismatch(ImageError):
    """The tree digest does not match manifest.digest."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_digest(image_path: Path) -> str:
    """Return ``sha256:<hex>`` over the image tree, excluding manifest.json."""
    root = Path(image_path)
    if root.is_symlink():
        raise ImageError(f"image path must not be a symlink: {root}")
    if not root.is_dir():
        raise ImageError(f"image directory does not exist: {root}")

    entries: list[tuple[str, str]] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for dirname in list(dirnames):
            candidate = base / dirname
            if candidate.is_symlink():
                raise ImageError(f"symlink directory is forbidden: {candidate}")
        for filename in filenames:
            candidate = base / filename
            if candidate.is_symlink():
                raise ImageError(f"symlink file is forbidden: {candidate}")
            if not candidate.is_file():
                raise ImageError(f"non-regular file is forbidden: {candidate}")
            relative = candidate.relative_to(root).as_posix()
            if relative == MANIFEST_NAME:
                continue
            entries.append((relative, sha256_file(candidate)))

    digest = hashlib.sha256()
    for relative, file_hex in sorted(entries):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hex))
        digest.update(b"\n")
    return DIGEST_PREFIX + digest.hexdigest()


def read_manifest(image_path: Path, expected_kind: str) -> dict[str, Any]:
    manifest_path = Path(image_path) / MANIFEST_NAME
    if manifest_path.is_symlink():
        raise ImageError(f"manifest must not be a symlink: {manifest_path}")
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ImageError(f"cannot read {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ImageError(f"{manifest_path}: manifest must be an object")
    if payload.get("kind") != expected_kind:
        raise ImageError(
            f"{manifest_path}: kind must be {expected_kind!r}, "
            f"got {payload.get('kind')!r}"
        )
    expected = payload.get("digest")
    if not isinstance(expected, str) or not expected.startswith(
        DIGEST_PREFIX
    ):
        raise ImageError(f"{manifest_path}: invalid digest field")
    if len(expected) != len(DIGEST_PREFIX) + 64:
        raise ImageError(f"{manifest_path}: invalid digest length")
    return payload


def verify_image(
    image_path: Path, expected_kind: str
) -> tuple[dict[str, Any], str]:
    """Verify the image digest; return ``(manifest, actual_digest)``."""
    manifest = read_manifest(image_path, expected_kind)
    actual = image_digest(image_path)
    expected = manifest["digest"]
    if not hmac.compare_digest(actual, expected):
        raise DigestMismatch(
            f"digest mismatch for {image_path}: "
            f"manifest={expected} actual={actual}"
        )
    return manifest, actual
