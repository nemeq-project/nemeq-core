"""Pure reconcile tests for the D-11 massaru Epishu controller.

No CLI, no direct instance.toml writes: reconcile returns actions and a
status; the test asserts the actions and that the filesystem stays untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from massaru.api_types import EpishuSpec, EpishuStatus
from massaru.images import image_digest
from massaru.reconcile import (
    EnsureLayout,
    ImageObservation,
    ObservedEpishu,
    Reject,
    UpdateStatus,
    WriteMaterialization,
    bind_model,
    materialize_instance,
    reconcile,
)


def make_image(
    tmp_path: Path,
    name: str,
    kind: str,
    files: dict[str, str] | None = None,
    endpoint_class: str = "opus",
) -> tuple[Path, str, dict]:
    image = tmp_path / name
    image.mkdir(parents=True)
    for relative, body in (files or {"data.txt": "payload"}).items():
        target = image / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    digest = image_digest(image)
    manifest = {"kind": kind, "name": name, "version": "1.0", "digest": digest}
    if kind == "su":
        manifest["endpoint_class"] = endpoint_class
    (image / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return image, digest, manifest


def observe(
    image: Path,
    kind: str,
    digest: str,
    manifest: dict,
    actual: str | None = None,
    error: str | None = None,
) -> ImageObservation:
    return ImageObservation(
        kind=kind,
        ref=str(image),
        expected_digest=digest,
        actual_digest=digest if actual is None else actual,
        manifest=manifest,
        error=error,
    )


def valid_observations(tmp_path: Path):
    zi, zi_d, zi_m = make_image(tmp_path, "zi", "zi")
    su, su_d, su_m = make_image(tmp_path, "su", "su")
    return zi, su, observe(zi, "zi", zi_d, zi_m), observe(su, "su", su_d, su_m)


def test_fresh_reconcile_returns_actions_without_writes(tmp_path: Path):
    state_root = tmp_path / "var"
    zi, su, zi_obs, su_obs = valid_observations(tmp_path)
    spec = EpishuSpec(zi_ref=str(zi), su_ref=str(su), model="opus")

    plan = reconcile(
        "ep-1",
        spec,
        EpishuStatus(),
        ObservedEpishu(zi=zi_obs, su=su_obs),
        state_root=state_root,
    )

    assert plan.error is None
    assert [type(action) for action in plan.actions] == [
        EnsureLayout,
        WriteMaterialization,
        UpdateStatus,
    ]
    layout = plan.actions[0]
    assert isinstance(layout, EnsureLayout)
    assert layout.name == "ep-1"
    assert "wal" in layout.directories
    assert "instance.toml" in layout.files
    write = plan.actions[1]
    assert isinstance(write, WriteMaterialization)
    assert write.payload["epishu_id"] == "ep-1"
    assert write.payload["zi_digest"] == zi_obs.expected_digest
    assert write.payload["su_digest"] == su_obs.expected_digest
    assert write.payload["model"] == "opus"
    assert write.payload["read_only"] is True
    assert plan.status.phase == "Reconciling"
    assert plan.status.layout_path == str(state_root / "epishu" / "ep-1")
    assert not state_root.exists()


def test_reconcile_is_idempotent_when_materialized(tmp_path: Path):
    state_root = tmp_path / "var"
    zi, su, zi_obs, su_obs = valid_observations(tmp_path)
    spec = EpishuSpec(zi_ref=str(zi), su_ref=str(su))
    first = reconcile(
        "ep-1",
        spec,
        EpishuStatus(),
        ObservedEpishu(zi=zi_obs, su=su_obs),
        state_root=state_root,
    )
    payload = first.actions[1].payload
    second = reconcile(
        "ep-1",
        spec,
        first.status,
        ObservedEpishu(
            layout_exists=True,
            materialized=payload,
            zi=zi_obs,
            su=su_obs,
        ),
        state_root=state_root,
    )
    assert all(type(action) is UpdateStatus for action in second.actions)
    third = reconcile(
        "ep-1",
        spec,
        second.status,
        ObservedEpishu(
            layout_exists=True,
            materialized=payload,
            zi=zi_obs,
            su=su_obs,
        ),
        state_root=state_root,
    )
    assert third.actions == ()
    assert third.status.phase == "Ready"


def test_digest_mismatch_rejects_without_layout(tmp_path: Path):
    state_root = tmp_path / "var"
    zi, su, zi_obs, su_obs = valid_observations(tmp_path)
    bad = ImageObservation(
        kind="zi",
        ref=zi_obs.ref,
        expected_digest="sha256:" + "0" * 64,
        actual_digest=zi_obs.actual_digest,
        manifest=zi_obs.manifest,
    )
    spec = EpishuSpec(zi_ref=str(zi), su_ref=str(su))
    plan = reconcile(
        "ep-1",
        spec,
        EpishuStatus(),
        ObservedEpishu(zi=bad, su=su_obs),
        state_root=state_root,
    )
    assert plan.error == "digest_mismatch"
    assert isinstance(plan.actions[0], Reject)
    assert plan.status.phase == "Failed"
    assert not any(isinstance(a, EnsureLayout) for a in plan.actions)
    assert not state_root.exists()


def test_tampered_image_actual_digest_rejects(tmp_path: Path):
    state_root = tmp_path / "var"
    zi, su, zi_obs, su_obs = valid_observations(tmp_path)
    (zi / "data.txt").write_text("tampered", encoding="utf-8")
    actual = image_digest(zi)
    assert actual != zi_obs.expected_digest
    tampered = observe(
        zi, "zi", zi_obs.expected_digest, zi_obs.manifest, actual=actual
    )
    spec = EpishuSpec(zi_ref=str(zi), su_ref=str(su))
    plan = reconcile(
        "ep-1",
        spec,
        EpishuStatus(),
        ObservedEpishu(zi=tampered, su=su_obs),
        state_root=state_root,
    )
    assert plan.error == "digest_mismatch"
    assert plan.actions[0].code == "digest_mismatch"
    assert not state_root.exists()


def test_bind_model_is_pure_spec_update(tmp_path: Path):
    spec = EpishuSpec(zi_ref="zi", su_ref="su", model="old")
    updated = bind_model(spec, "new-model")
    assert updated.model == "new-model"
    assert spec.model == "old"
    with pytest.raises(ValueError):
        bind_model(spec, "")
    assert not (tmp_path / "var").exists()


def test_read_only_and_kind_guards(tmp_path: Path):
    zi, su, zi_obs, su_obs = valid_observations(tmp_path)
    observed = ObservedEpishu(zi=zi_obs, su=su_obs)
    writable = EpishuSpec(
        zi_ref=str(zi), su_ref=str(su), read_only=False
    )
    assert (
        reconcile("ep-1", writable, observed=observed).error
        == "read_only_violation"
    )
    bad_kind = EpishuSpec(zi_ref=str(zi), su_ref=str(su), kind="massaru")
    assert reconcile("ep-1", bad_kind, observed=observed).error == "invalid_kind"


def test_materialize_instance_includes_endpoint_class(tmp_path: Path):
    zi, su, zi_obs, su_obs = valid_observations(tmp_path)
    spec = EpishuSpec(zi_ref=str(zi), su_ref=str(su))
    payload = materialize_instance("ep-1", spec, zi_obs, su_obs)
    assert payload["endpoint_class"] == "opus"
    assert payload["schema"] == 1
    assert payload["state"] == "dormant"
