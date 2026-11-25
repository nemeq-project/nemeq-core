import pytest
from apiserver.rbac import grant_allows, authorize, denies_leader_change, VERBS, ROLE_POWER


def test_grant_allows_roles():
    viewer = {"spec": {"subject": "a", "role": "viewer", "scope": "epishu/o", "verbs": []}}
    assert grant_allows(viewer, "get", "epishu/o") is True
    assert grant_allows(viewer, "delete", "epishu/o") is False
    admin = {"spec": {"subject": "r", "role": "admin", "scope": "*", "verbs": []}}
    assert grant_allows(admin, "delete", "cluster") is True
    assert grant_allows(None, "get", "cluster") is False
    assert ROLE_POWER["admin"] > ROLE_POWER["viewer"]


def test_grant_scope_mismatch():
    g = {"spec": {"subject": "a", "role": "ops", "scope": "epishu/one", "verbs": []}}
    assert grant_allows(g, "create", "epishu/two") is False
    starred = {"spec": {"subject": "a", "role": "ops", "scope": "*", "verbs": []}}
    assert grant_allows(starred, "create", "epishu/two") is True


def test_authorize_subject_filter():
    a = {"spec": {"subject": "alice", "role": "viewer", "scope": "*", "verbs": []}}
    b = {"spec": {"subject": "bob", "role": "admin", "scope": "*", "verbs": []}}
    assert authorize([a, b], "get", "cluster", subject="alice") is True
    assert authorize([a], "delete", "cluster", subject="alice") is False
    assert authorize([a, b], "delete", "cluster", subject="bob") is True


def test_denies_leader_change():
    assert denies_leader_change({"spec": {"role": "viewer"}}, {"spec": {"role": "admin"}}) is True
    assert denies_leader_change(
        {"spec": {"role": "viewer", "subject": "a"}},
        {"spec": {"role": "viewer", "subject": "a"}},
    ) is False
