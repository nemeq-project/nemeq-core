"""Round-trip and defaults for the nemeq.io/v1alpha1 dataclasses."""

from __future__ import annotations

from massaru.api_types import (
    Epishu,
    EpishuSpec,
    EpishuStatus,
    Grant,
    GrantSpec,
    GrantStatus,
    Model,
    ModelSpec,
    ModelStatus,
    Quota,
    QuotaSpec,
    QuotaStatus,
    Session,
    SessionSpec,
    SessionStatus,
    Su,
    SuSpec,
    SuStatus,
    Zi,
    ZiSpec,
    ZiStatus,
)


def test_epishu_to_dict_and_roundtrip() -> None:
    original = Epishu(
        name="e",
        spec=EpishuSpec(zi_ref="zi", su_ref="su"),
        status=EpishuStatus(phase="Pending"),
        resource_version=7,
        uid="uid-1",
    )
    assert original.rev == 7
    payload = original.to_dict()
    assert payload["apiVersion"] == "nemeq.io/v1alpha1"
    assert payload["kind"] == "Epishu"
    assert payload["name"] == "e"
    assert payload["uid"] == "uid-1"
    assert payload["spec"]["zi_ref"] == "zi"
    assert payload["status"]["phase"] == "Pending"
    assert payload["resourceVersion"] == 7

    restored = Epishu.from_dict(payload)
    assert restored == original


def test_all_resource_roundtrips() -> None:
    objects = [
        Zi(
            name="z1",
            spec=ZiSpec(image="zi.img", digest="sha256:" + "1" * 64),
            status=ZiStatus(phase="Ready", verified=True),
            resource_version=3,
        ),
        Su(
            name="s1",
            spec=SuSpec(
                image="su.img",
                digest="sha256:" + "2" * 64,
                capabilities=["tool:nd"],
                endpoint_class="opus",
            ),
            status=SuStatus(phase="Ready", verified=True),
            resource_version=3,
        ),
        Epishu(
            name="e1",
            spec=EpishuSpec(zi_ref="z1", su_ref="s1", model="opus"),
            status=EpishuStatus(phase="Ready"),
            resource_version=3,
        ),
        Session(
            name="ses1",
            spec=SessionSpec(
                epishu_ref="e1", principal="p", client_id="c"
            ),
            status=SessionStatus(phase="Active", queue_pos=1),
            resource_version=3,
        ),
        Grant(
            name="g1",
            spec=GrantSpec(
                subject="alice",
                role="viewer",
                scope="e1",
                verbs=["get"],
            ),
            status=GrantStatus(),
            resource_version=3,
        ),
        Quota(
            name="q1",
            spec=QuotaSpec(scope="e1", tokens=10, cost=1.5),
            status=QuotaStatus(
                used={"tokens": 2}, limit={"tokens": 10}
            ),
            resource_version=3,
        ),
        Model(
            name="m1",
            spec=ModelSpec(
                provider="local", model_id="opus", context=1000
            ),
            status=ModelStatus(healthy=True, version="1"),
            resource_version=3,
        ),
    ]
    for obj in objects:
        assert type(obj).from_dict(obj.to_dict()) == obj


def test_defaults() -> None:
    spec = EpishuSpec(zi_ref="zi", su_ref="su")
    assert spec.read_only is True
    assert spec.kind == "awilu"
    assert spec.model == ""
    assert SessionStatus().phase == "Queued"
    assert QuotaStatus().used == {}
    assert QuotaStatus().limit == {}
    assert EpishuStatus().conditions == []
    assert EpishuStatus().phase == "Pending"


def test_from_dict_defaults_resource_version() -> None:
    payload = {
        "name": "e",
        "spec": {"zi_ref": "zi", "su_ref": "su"},
        "status": {},
    }
    obj = Epishu.from_dict(payload)
    assert obj.resource_version == 0
    assert obj.status.phase == "Pending"
