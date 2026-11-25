import pytest
from apiserver._testkit import API, ADMIN, call, server  # noqa: F401


def make_grant(store, name, subject, role, scope, verbs):
    from apiserver.handlers import Api
    Api(store, lambda: [ADMIN]).create(
        "grants",
        {"name": name, "spec": {"subject": subject, "role": role, "scope": scope, "verbs": verbs}},
        actor="root",
    )


def test_viewer_with_delete_cannot_delete(server):
    s = server[0]
    call(s, "POST", f"{API}/zis", body={"name": "z1", "spec": {"image": "i", "digest": "d"}}, actor="root")
    make_grant(server[1], "v1", "v", "viewer", "*", ["delete"])
    status, body = call(s, "DELETE", f"{API}/zis/z1", actor="v")
    assert status == 403 and body["reason"] == "Forbidden"


def test_viewer_with_create_cannot_create(server):
    make_grant(server[1], "v2", "v2", "viewer", "*", ["create"])
    status, body = call(server[0], "POST", f"{API}/zis",
                        body={"name": "z9", "spec": {"image": "i", "digest": "d"}}, actor="v2")
    assert status == 403 and body["reason"] == "Forbidden"


def test_ops_status_requires_explicit_status(server):
    s = server[0]
    call(s, "POST", f"{API}/epishus", body={"name": "e1", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    make_grant(server[1], "o1", "o1", "ops", "epishu/e1", ["get", "update"])
    status, _ = call(s, "PUT", f"{API}/epishus/e1/status",
                     body={"status": {"phase": "Ready"}}, actor="o1")
    assert status == 403
    make_grant(server[1], "o2", "o2", "ops", "epishu/e1", ["get", "status"])
    status, body = call(s, "PUT", f"{API}/epishus/e1/status",
                        body={"status": {"phase": "Ready"}}, actor="o2")
    assert status == 200 and body["status"]["phase"] == "Ready"


def test_escalation_attempt_is_audited(server):
    make_grant(server[1], "v3", "v3", "viewer", "*", ["delete"])
    call(server[0], "DELETE", f"{API}/zis/none", actor="v3")
    audits, _ = server[1].list("audits/")
    assert any(a["auditDeny"]["status"] == 403 and a["auditDeny"]["actor"] == "v3"
               for a in audits)
