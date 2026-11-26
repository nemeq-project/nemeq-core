import pytest
from apiserver._testkit import API, call, server, ADMIN  # noqa: F401


def make_grant(store, name, subject, role, scope, verbs):
    spec = {"subject": subject, "role": role, "scope": scope, "verbs": verbs}
    from apiserver.handlers import Api
    Api(store, lambda: [ADMIN]).create("grants", {"name": name, "spec": spec}, actor="root")


def test_get_only_actor_cannot_list(server):
    make_grant(server[1], "g", "g", "viewer", "*", ["get"])
    status, body = call(server[0], "GET", f"{API}/zis", actor="g")
    assert status == 403 and body["reason"] == "Forbidden"


def test_get_only_actor_cannot_watch(server):
    make_grant(server[1], "g2", "g2", "viewer", "*", ["get"])
    status, body = call(server[0], "GET", f"{API}/zis?watch=1&from_rev=0", actor="g2")
    assert status == 403 and body["reason"] == "Forbidden"


def test_list_only_actor_can_list_not_get(server):
    make_grant(server[1], "l", "l", "viewer", "*", ["list"])
    call(server[0], "POST", f"{API}/zis",
         body={"name": "z1", "spec": {"image": "i", "digest": "d"}}, actor="root")
    status, body = call(server[0], "GET", f"{API}/zis", actor="l")
    assert status == 200 and len(body["items"]) == 1
    status, _ = call(server[0], "GET", f"{API}/zis/z1", actor="l")
    assert status == 403


def test_watch_only_actor_can_watch_not_list(server):
    make_grant(server[1], "w", "w", "viewer", "*", ["watch"])
    status, body = call(server[0], "GET", f"{API}/zis?watch=1&from_rev=0", actor="w")
    assert status == 200 and "metadata" in body
    status, _ = call(server[0], "GET", f"{API}/zis", actor="w")
    assert status == 403


def test_collection_denial_writes_audit(server):
    make_grant(server[1], "g3", "g3", "viewer", "*", ["get"])
    call(server[0], "GET", f"{API}/zis", actor="g3")
    audits, _ = server[1].list("audits/")
    assert any(
        a["auditDeny"]["verb"] == "list" and a["auditDeny"]["status"] == 403
        for a in audits
    )


def test_scoped_list_still_filters(server):
    make_grant(server[1], "s", "s", "viewer", "epishu/one", ["list"])
    for n in ("one", "two"):
        call(server[0], "POST", f"{API}/epishus",
             body={"name": n, "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    status, body = call(server[0], "GET", f"{API}/epishus", actor="s")
    assert status == 200
    assert [i["metadata"]["name"] for i in body["items"]] == ["one"]
