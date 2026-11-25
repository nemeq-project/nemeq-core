import pytest
from apiserver.admission import admit, check_read_only, WRITE_CAPABILITIES, FORBIDDEN_KINDS
from apiserver.resources import (Reject, defaults, validate_spec, validate_object,
                                  validate_name, RESOURCES, PLURALS, STATUS_SUBRESOURCE,
                                  key_for)

ADMIN = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}


def test_resources_registry():
    assert len(RESOURCES) == 7
    assert PLURALS == {v: k for k, v in RESOURCES.items()}
    assert STATUS_SUBRESOURCE == frozenset({"epishus", "sessions", "quotas"})
    assert key_for("epishus", "Octavia") == "epishus/octavia"


@pytest.mark.parametrize("name", ["", "Bad", "a/b", "-x", "x" * 64, "../escape"])
def test_validate_name_rejects_bad(name):
    with pytest.raises(Reject) as exc:
        validate_name(name)
    assert exc.value.status == 422
    validate_name("abc-1.2")


def test_validate_spec_requires_fields():
    for kind, fields in {"Zi": ("image", "digest"), "Su": ("image", "digest"),
                         "Epishu": ("ziRef", "suRef"), "Session": ("epishuRef", "principal"),
                         "Grant": ("subject", "role", "scope", "verbs"), "Quota": ("scope",),
                         "Model": ("provider", "modelId")}.items():
        with pytest.raises(Reject) as exc:
            validate_spec(kind, {})
        assert exc.value.status == 422
    validate_spec("Epishu", {"ziRef": "z", "suRef": "s"})


def test_defaults_epishu():
    original = {"ziRef": "z", "suRef": "s"}
    result = defaults("Epishu", original)
    assert result["readOnly"] is True
    assert result["kind"] == "awilu"
    assert result["quotas"] == {}
    assert original == {"ziRef": "z", "suRef": "s"}


def test_validate_spec_types():
    with pytest.raises(Reject):
        validate_spec("Zi", {"image": "i", "digest": "d", "readOnly": "yes"})
    with pytest.raises(Reject):
        validate_spec("Quota", {"scope": "s", "tokens": -1})
    with pytest.raises(Reject):
        validate_spec("Quota", {"scope": "s", "concurrency": 0})
    with pytest.raises(Reject):
        validate_spec("Quota", {"scope": "s", "cost": -1})
    with pytest.raises(Reject):
        validate_spec("Model", {"provider": "p", "modelId": "m", "context": -1})
    r = validate_spec("Quota", {"scope": "s", "tokens": 10, "cost": 1.5, "concurrency": 2})
    assert r["tokens"] == 10
    r = validate_spec("Zi", {"image": "i", "digest": "d", "extra": 1})
    assert r["extra"] == 1


def test_validate_object_requires_name_and_spec():
    with pytest.raises(Reject):
        validate_object("Zi", {})
    with pytest.raises(Reject):
        validate_object("Zi", {"name": "x"})
    with pytest.raises(Reject):
        validate_object("Model", {"name": "x", "spec": {}})


def test_read_only_policy():
    assert check_read_only("Effect", {}) is not None
    assert check_read_only("Su", {"capabilities": ["exec"]}) is not None
    assert check_read_only("Su", {"capabilities": ["tool:nd"]}) is None
    assert check_read_only("Epishu", {"readOnly": False}) is not None
    assert check_read_only("Epishu", {"readOnly": True}) is None


def test_admit_success_returns_defaults():
    body = {"name": "o", "spec": {"ziRef": "z", "suRef": "s"}}
    d = admit("Epishu", body, verb="create", grants=[ADMIN], quotas=[], actor="root")
    assert d.allowed is True
    assert d.status == 200
    assert d.spec.get("readOnly") is True
    assert d.audit is None


def test_admit_rejects_rbac():
    body = {"name": "o", "spec": {"ziRef": "z", "suRef": "s"}}
    d = admit("Epishu", body, verb="create", grants=[], quotas=[], actor="root")
    assert d.allowed is False
    assert d.status == 403
    assert d.audit is not None
    assert "verb" in d.audit and "kind" in d.audit and "name" in d.audit


def test_admit_rejects_effect():
    body = {"name": "e", "spec": {}}
    d = admit("Effect", body, verb="create", grants=[ADMIN], quotas=[], actor="root")
    assert d.allowed is False
    assert d.status == 403


def test_admit_quota():
    q = {
        "spec": {"scope": "epishu/o"},
        "status": {"used": {"tokens": 5}, "limit": {"tokens": 5}},
    }
    body = {"name": "o", "spec": {"ziRef": "z", "suRef": "s"}}
    d = admit("Epishu", body, verb="create", grants=[ADMIN], quotas=[q], actor="root")
    assert d.allowed is False
    assert d.status == 429
    assert "_name" not in d.spec


def test_admit_grant_leader_change():
    prev = {"spec": {"subject": "a", "role": "viewer", "scope": "*", "verbs": ["*"]}}
    body = {"name": "g1", "spec": {"subject": "a", "role": "admin", "scope": "*", "verbs": ["*"]}}
    d = admit("Grant", body, verb="update", grants=[ADMIN], quotas=[], actor="root", prev=prev)
    assert d.allowed is False
    assert d.status == 403
