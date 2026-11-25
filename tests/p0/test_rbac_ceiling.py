import pytest
from apiserver.rbac import ROLE_POWER, STATUS_ROLES, VERBS, authorize, grant_allows, grant_verb_allows


def grant_for(role, verbs=None, scope="*"):
    spec = {"role": role, "scope": scope}
    if verbs is not None:
        spec["verbs"] = verbs
    return {"spec": spec}


def g(subject, role, verbs=None, scope="*"):
    spec = {"subject": subject, "role": role, "scope": scope}
    if verbs is not None:
        spec["verbs"] = verbs
    return {"spec": spec}


def test_authorize_uses_role_ceiling():
    assert authorize([g("v", "viewer", ["delete"])], "delete", "cluster", subject="v") is False
    assert authorize([g("v", "viewer", ["create"])], "create", "cluster", subject="v") is False
    assert authorize([g("v", "viewer", ["*"])], "delete", "cluster", subject="v") is False
    assert authorize([g("v", "viewer", ["get"])], "get", "cluster", subject="v") is True
    assert authorize([g("v", "viewer", ["get"])], "list", "cluster", subject="v") is False


def test_authorize_status_only_explicit_for_ops():
    assert authorize([g("o", "ops", ["status"])], "status", "cluster", subject="o") is True
    assert authorize([g("o", "ops")], "status", "cluster", subject="o") is False
    assert authorize([g("o", "ops", ["update"])], "status", "cluster", subject="o") is False
    assert authorize([g("v", "viewer", ["status"])], "status", "cluster", subject="v") is False


def test_authorize_scope_and_subject():
    assert authorize([g("v", "ops", ["get"], "epishu/one")], "get", "epishu/two", subject="v") is False
    assert authorize([g("v", "ops", ["get"], "epishu/one")], "get", "epishu/one", subject="v") is True
    assert authorize([g("v", "ops", ["get"])], "get", "cluster", subject="other") is False


def test_defaults_preserved_when_verbs_empty():
    assert authorize([g("v", "viewer", [])], "list", "cluster", subject="v") is True
    assert authorize([g("v", "viewer")], "watch", "cluster", subject="v") is True
    assert authorize([g("o", "ops", [])], "delete", "cluster", subject="o") is True


def test_effective_verbs_is_single_source():
    roles = ("viewer", "ops", "admin_epishu", "admin")
    verbs = ("get", "list", "watch", "create", "update", "delete", "status", "adminscope")
    for role in roles:
        for verbs_spec in (None, [], ["get"], ["delete"], ["status"], ["*"]):
            grant = g("s", role, verbs_spec)
            for verb in verbs:
                via_authorize = authorize([grant], verb, "cluster", subject="s")
                via_grant = grant_allows(grant, verb, "cluster")
                assert via_authorize == via_grant, (role, verbs_spec, verb)


def test_admin_wildcard_and_power_ordering():
    admin_star = grant_for("admin", ["*"])
    for verb in ("delete", "status", "create", "update", "get", "list", "watch"):
        assert grant_allows(admin_star, verb, "cluster") is True
    admin_get = grant_for("admin", ["get"])
    assert grant_allows(admin_get, "get", "cluster") is True
    for verb in ("delete", "status", "create"):
        assert grant_allows(admin_get, verb, "cluster") is False
    assert grant_verb_allows(admin_star, "delete") is True
    assert ROLE_POWER["admin"] > ROLE_POWER["admin_epishu"] > ROLE_POWER["ops"] > ROLE_POWER["viewer"]
    assert "status" not in VERBS["viewer"]
    assert STATUS_ROLES == frozenset({"ops", "admin_epishu", "admin"})
    viewer_star = grant_for("viewer", ["*"])
    for verb in ("delete", "create", "update", "status"):
        assert grant_allows(viewer_star, verb, "cluster") is False
