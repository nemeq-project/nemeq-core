"""Pure reconciliation for the massaru Epishu controller (D-11).

The apiserver object is the source of truth.  This module computes actions
from ``spec/status`` and observations; it performs no filesystem writes and
no process operations.  A future controller executes the actions and writes
``status`` through the API.

Image digests are observed read-only (``images.verify_image``) and passed in
as ``ImageObservation``.  A wrong digest or a tampered tree yields a Reject
action and phase=Failed; no layout/materialization actions are emitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .api_types import EpishuSpec, EpishuStatus
from .layout import (
    STANDARD_DIRS,
    STANDARD_FILES,
    epishu_dir,
    resolve_state_root,
    safe_epishu_id,
)

ALLOWED_KINDS = {"awilu", "subur"}


@dataclass(frozen=True)
class ImageObservation:
    kind: str
    ref: str
    expected_digest: str
    actual_digest: str | None
    manifest: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class ObservedEpishu:
    layout_exists: bool = False
    materialized: dict[str, Any] | None = None
    zi: ImageObservation | None = None
    su: ImageObservation | None = None


@dataclass(frozen=True)
class Action:
    def describe(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class EnsureLayout(Action):
    name: str
    path: str
    directories: tuple[str, ...] = STANDARD_DIRS
    files: tuple[str, ...] = STANDARD_FILES


@dataclass(frozen=True)
class WriteMaterialization(Action):
    name: str
    path: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UpdateStatus(Action):
    status: EpishuStatus


@dataclass(frozen=True)
class Reject(Action):
    code: str
    message: str


@dataclass(frozen=True)
class ReconcilePlan:
    actions: tuple[Action, ...]
    status: EpishuStatus
    error: str | None = None


def bind_model(spec: EpishuSpec, model: str) -> EpishuSpec:
    """Pure spec update for bind_model; no files, no API calls."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    return replace(spec, model=model)


def materialize_instance(
    name: str,
    spec: EpishuSpec,
    zi: ImageObservation,
    su: ImageObservation,
) -> dict[str, Any]:
    """Return the instance.toml-shaped materialization (no write)."""
    endpoint_class = "default"
    if isinstance(su.manifest, dict):
        candidate = su.manifest.get("endpoint_class", "default")
        if isinstance(candidate, str) and candidate:
            endpoint_class = candidate
    zi_name = ""
    su_name = ""
    if isinstance(zi.manifest, dict):
        zi_name = str(zi.manifest.get("name", ""))
    if isinstance(su.manifest, dict):
        su_name = str(su.manifest.get("name", ""))
    return {
        "schema": 1,
        "epishu_id": name,
        "state": "dormant",
        "zi_ref": spec.zi_ref,
        "zi_name": zi_name,
        "zi_digest": zi.expected_digest,
        "su_ref": spec.su_ref,
        "su_name": su_name,
        "su_digest": su.expected_digest,
        "endpoint_class": endpoint_class,
        "model": spec.model,
        "read_only": bool(spec.read_only),
        "generation": 0,
    }


def _failed(
    spec: EpishuSpec, code: str, message: str
) -> ReconcilePlan:
    status = EpishuStatus(
        phase="Failed",
        conditions=[
            {
                "type": "Ready",
                "status": "False",
                "reason": code,
                "message": message,
            }
        ],
        model=spec.model,
    )
    return ReconcilePlan(
        actions=(Reject(code=code, message=message), UpdateStatus(status)),
        status=status,
        error=code,
    )


def reconcile(
    name: str,
    spec: EpishuSpec,
    status: EpishuStatus | None = None,
    observed: ObservedEpishu | None = None,
    state_root: Path | None = None,
) -> ReconcilePlan:
    """Pure Epishu reconciliation.

    Returns ordered actions (layout, materialization, status) or a Reject.
    The caller (apiserver controller loop) performs all effects and writes
    status through the API.
    """
    if state_root is None:
        state_root = resolve_state_root()
    current = status or EpishuStatus()
    seen = observed or ObservedEpishu()

    if spec.kind not in ALLOWED_KINDS:
        return _failed(spec, "invalid_kind", f"kind {spec.kind!r} is not allowed")
    if spec.read_only is not True:
        return _failed(
            spec,
            "read_only_violation",
            "v1 Epishu must be read_only=true",
        )
    try:
        safe_epishu_id(name)
        path = str(epishu_dir(state_root, name))
    except ValueError as exc:
        return _failed(spec, "invalid_name", str(exc))

    for kind, observation in (("zi", seen.zi), ("su", seen.su)):
        if observation is None:
            return _failed(spec, f"{kind}_missing", f"{kind} observation missing")
        if observation.error:
            return _failed(
                spec, f"{kind}_error", observation.error
            )
        if observation.kind != kind:
            return _failed(
                spec,
                f"{kind}_kind_mismatch",
                f"expected {kind}, got {observation.kind}",
            )
        if (
            observation.actual_digest is None
            or observation.actual_digest != observation.expected_digest
        ):
            return _failed(
                spec,
                "digest_mismatch",
                f"{kind} digest mismatch: expected="
                f"{observation.expected_digest} "
                f"actual={observation.actual_digest}",
            )

    assert seen.zi is not None and seen.su is not None
    payload = materialize_instance(name, spec, seen.zi, seen.su)
    actions: list[Action] = []
    if not seen.layout_exists:
        actions.append(
            EnsureLayout(
                name=name,
                path=path,
                directories=tuple(STANDARD_DIRS),
                files=tuple(STANDARD_FILES),
            )
        )
    if seen.materialized != payload:
        actions.append(
            WriteMaterialization(
                name=name, path=path, payload=payload
            )
        )

    desired = EpishuStatus(
        phase="Reconciling" if actions else "Ready",
        node=current.node,
        session_ref=current.session_ref,
        conditions=[],
        observed_generation=current.observed_generation,
        layout_path=path,
        zi_digest=seen.zi.expected_digest,
        su_digest=seen.su.expected_digest,
        model=spec.model,
    )
    if desired != current:
        actions.append(UpdateStatus(desired))
    return ReconcilePlan(actions=tuple(actions), status=desired)
