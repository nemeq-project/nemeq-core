"""Massaru instance layout, install, digest verification and bind_model.

v1 slice:
- install verifies zi/su manifests and tree digests before creating anything;
- instance.toml records refs, digests, endpoint class and model;
- bind_model re-verifies digests and updates only the model field;
- verify is a read-only tamper check.

run/sleep/stop/commit remain explicit stubs in the CLI until the lifecycle
slice.  Nothing here performs an external side effect.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any

from .images import DigestMismatch, ImageError, verify_image
from .layout import (
    STANDARD_DIRS,
    epishu_dir,
    epishu_root,
    instance_file,
    safe_epishu_id,
)

INSTANCE_NAME = "instance.toml"
TUSH_NAME = "tush.toml"
INSTANCE_KEYS = (
    "schema",
    "epishu_id",
    "state",
    "zi_ref",
    "zi_name",
    "zi_digest",
    "su_ref",
    "su_name",
    "su_digest",
    "endpoint_class",
    "model",
    "generation",
    "created_at",
)


class InstanceError(ValueError):
    """Base class for massaru instance refusals."""


class InstanceMissing(InstanceError):
    pass


class InstanceExists(InstanceError):
    pass


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def _toml_document(payload: dict[str, Any]) -> str:
    lines = []
    for key in INSTANCE_KEYS:
        if key in payload:
            lines.append(f"{key} = {_toml_value(payload[key])}")
    return "\n".join(lines) + "\n"


def _write_text_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_instance(state_root: Path, epishu_id: str) -> dict[str, Any] | None:
    path = instance_file(state_root, epishu_id)
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _ensure_layout(base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    for name in STANDARD_DIRS:
        directory = base / name
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    for name in ("claims.jsonl", "settlement.jsonl"):
        path = base / name
        path.touch(mode=0o600, exist_ok=True)
        os.chmod(path, 0o600)


def _instance_payload(
    epishu_id: str,
    zi_ref: Path,
    zi_manifest: dict[str, Any],
    zi_digest: str,
    su_ref: Path,
    su_manifest: dict[str, Any],
    su_digest: str,
) -> dict[str, Any]:
    endpoint_class = su_manifest.get("endpoint_class", "default")
    if not isinstance(endpoint_class, str) or not endpoint_class:
        raise ImageError("su.manifest.endpoint_class must be a non-empty string")
    return {
        "schema": 1,
        "epishu_id": epishu_id,
        "state": "dormant",
        "zi_ref": str(zi_ref),
        "zi_name": str(zi_manifest.get("name", "")),
        "zi_digest": zi_digest,
        "su_ref": str(su_ref),
        "su_name": str(su_manifest.get("name", "")),
        "su_digest": su_digest,
        "endpoint_class": endpoint_class,
        "model": "",
        "generation": 0,
        "created_at": time.time(),
    }


def _write_instance(state_root: Path, payload: dict[str, Any]) -> None:
    path = instance_file(state_root, payload["epishu_id"])
    _write_text_atomic(path, _toml_document(payload))


def _write_tush(state_root: Path, epishu_id: str) -> None:
    tush = {
        "schema": 1,
        "epishu_id": epishu_id,
        "incarnation_id": uuid.uuid4().hex,
        "generation": 0,
    }
    _write_text_atomic(
        instance_file(state_root, epishu_id).parent / TUSH_NAME,
        "\n".join(
            f"{key} = {_toml_value(value)}" for key, value in tush.items()
        )
        + "\n",
    )


def install_instance(
    state_root: Path,
    zi_path: str | Path,
    su_path: str | Path,
    epishu_id: str | None = None,
) -> dict[str, Any]:
    zi_ref = Path(zi_path).resolve()
    su_ref = Path(su_path).resolve()
    zi_manifest, zi_digest = verify_image(zi_ref, "zi")
    su_manifest, su_digest = verify_image(su_ref, "su")

    if epishu_id is None:
        epishu_id = uuid.uuid4().hex
    safe_epishu_id(epishu_id)
    base = epishu_dir(state_root, epishu_id)
    existing = read_instance(state_root, epishu_id)
    if existing is not None:
        if (
            existing.get("zi_digest") == zi_digest
            and existing.get("su_digest") == su_digest
            and existing.get("zi_ref") == str(zi_ref)
            and existing.get("su_ref") == str(su_ref)
        ):
            return existing
        raise InstanceExists(f"instance already exists: {epishu_id}")
    if base.exists():
        raise InstanceExists(f"instance directory already exists: {epishu_id}")

    payload = _instance_payload(
        epishu_id,
        zi_ref,
        zi_manifest,
        zi_digest,
        su_ref,
        su_manifest,
        su_digest,
    )
    _ensure_layout(base)
    root = epishu_root(state_root)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    _write_instance(state_root, payload)
    _write_tush(state_root, epishu_id)
    return payload


def _verify_record(instance: dict[str, Any]) -> None:
    for kind, ref_key, digest_key in (
        ("zi", "zi_ref", "zi_digest"),
        ("su", "su_ref", "su_digest"),
    ):
        reference = instance.get(ref_key)
        expected = instance.get(digest_key)
        if not isinstance(reference, str) or not isinstance(expected, str):
            raise ImageError(f"{kind} reference/digest missing")
        _, actual = verify_image(Path(reference), kind)
        if actual != expected:
            raise DigestMismatch(
                f"{kind} digest changed: stored={expected} actual={actual}"
            )


def verify_instance(state_root: Path, epishu_id: str) -> dict[str, Any]:
    instance = read_instance(state_root, epishu_id)
    if instance is None:
        raise InstanceMissing(f"instance not found: {epishu_id}")
    _verify_record(instance)
    return instance


def bind_model(
    state_root: Path, epishu_id: str, model: str
) -> dict[str, Any]:
    if not isinstance(model, str) or not model.strip():
        raise InstanceError("model must be a non-empty string")
    instance = verify_instance(state_root, epishu_id)
    instance["model"] = model
    _write_instance(state_root, instance)
    return instance


def instance_summary(
    state_root: Path, epishu_id: str
) -> dict[str, Any] | None:
    instance = read_instance(state_root, epishu_id)
    if instance is None:
        return None
    return {
        "epishu_id": epishu_id,
        "path": str(epishu_dir(state_root, epishu_id)),
        "instance": instance,
    }
