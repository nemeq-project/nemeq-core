import json
import pytest
from apiserver._testkit import API, ADMIN, call, server  # noqa: F401


def grant(store, name, subject, role, scope, verbs=None):
    from apiserver.handlers import Api

    spec = {"subject": subject, "role": role, "scope": scope}
    verbs = verbs if verbs is not None else ["get", "list", "watch"]
    spec["verbs"] = verbs
    admin = {"name": "admin", "spec": {"subject": "root", "role": "admin",
                                       "scope": "*", "verbs": ["*"]}}
    api = Api(store, lambda: [admin])
    api.create("grants", {"name": name, "spec": spec}, actor="root")


def test_no_actor_denied(server):
    s = server[0]
    status, body = call(s, "GET", f"{API}/zis", actor="")
    assert status == 403 and body["reason"] == "Forbidden"
    status, body = call(s, "GET", f"{API}/zis/a", actor="")
    assert status == 403 and body["reason"] == "Forbidden"
    status, body = call(s, "DELETE", f"{API}/zis/a", actor="")
    assert status == 403 and body["reason"] == "Forbidden"


def test_actor_without_grant_denied(server):
    s = server[0]
    status, _ = call(s, "GET", f"{API}/zis", actor="ghost")
    assert status == 403
    status, _ = call(s, "POST", f"{API}/zis", body={"name": "g1", "spec": {"image": "i", "digest": "d"}}, actor="ghost")
    assert status == 403
    audits, _ = server[1].list("audits/")
    assert len(audits) >= 1
    assert audits[0]["auditDeny"]["actor"] == "ghost"


def test_viewer_cannot_write(server):
    grant(server[1], "viewer1", "v", "viewer", "*")
    s = server[0]
    call(s, "POST", f"{API}/zis", body={"name": "z1", "spec": {"image": "i", "digest": "d"}}, actor="root")
    status, _ = call(s, "PUT", f"{API}/zis/z1",
                     body={"name": "z1", "spec": {"image": "i", "digest": "d"}}, actor="v")
    assert status == 403
    status, _ = call(s, "DELETE", f"{API}/zis/z1", actor="v")
    assert status == 403
    call(s, "POST", f"{API}/epishus",
         body={"name": "e1", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    status, _ = call(s, "PUT", f"{API}/epishus/e1/status",
                     body={"status": {"phase": "Ready"}}, actor="v")
    assert status == 403


def test_viewer_reads_allowed(server):
    grant(server[1], "viewer2", "v", "viewer", "*")
    s = server[0]
    call(s, "POST", f"{API}/zis", body={"name": "z1", "spec": {"image": "i", "digest": "d"}}, actor="root")
    call(s, "POST", f"{API}/zis", body={"name": "z2", "spec": {"image": "i", "digest": "d"}}, actor="root")
    status, body = call(s, "GET", f"{API}/zis", actor="v")
    assert status == 200 and len(body["items"]) == 2
    status, _ = call(s, "GET", f"{API}/zis/z1", actor="v")
    assert status == 200


def test_scoped_viewer_sees_only_scope(server):
    grant(server[1], "vv", "vv", "viewer", "epishu/one")
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "one", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    call(s, "POST", f"{API}/epishus",
         body={"name": "two", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    status, body = call(s, "GET", f"{API}/epishus", actor="vv")
    assert status == 200 and len(body["items"]) == 1 and body["items"][0]["metadata"]["name"] == "one"
    status, _ = call(s, "GET", f"{API}/epishus/two", actor="vv")
    assert status == 403
    status, _ = call(s, "GET", f"{API}/epishus/one", actor="vv")
    assert status == 200


def test_ops_cannot_set_status_without_status_verb(server):
    grant(server[1], "o", "o", "ops", "epishu/one",
          verbs=["get", "list", "watch", "create", "update", "delete"])
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "one", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    status, _ = call(s, "PUT", f"{API}/epishus/one",
                     body={"name": "one", "spec": {"ziRef": "z2", "suRef": "s"}}, actor="o")
    assert status == 200
    status, _ = call(s, "PUT", f"{API}/epishus/one/status",
                     body={"status": {"phase": "Ready"}}, actor="o")
    assert status == 403


def test_status_verb_grant_allows_status(server):
    grant(server[1], "s", "s", "ops", "epishu/one", verbs=["get", "status"])
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "one", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    status, body = call(s, "PUT", f"{API}/epishus/one/status",
                        body={"status": {"phase": "Ready"}}, actor="s")
    assert status == 200 and body["status"]["phase"] == "Ready"


def test_watch_filtered_by_scope(server):
    grant(server[1], "w", "w", "viewer", "epishu/one")
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "one", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    call(s, "POST", f"{API}/epishus",
         body={"name": "two", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    status, body = call(s, "GET", f"{API}/epishus?watch=1&from_rev=0", actor="w")
    assert status == 200
    for item in body["items"]:
        if item.get("object") is not None:
            assert item["object"]["metadata"]["name"] == "one"


def test_denied_operation_creates_audit(server):
    grant(server[1], "v", "v", "viewer", "epishu/one")
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "one", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    call(s, "DELETE", f"{API}/epishus/one", actor="v")
    audits, _ = server[1].list("audits/")
    last = sorted(audits, key=lambda x: list(x.keys()))[-1]
    deny = last["auditDeny"]
    assert deny["verb"] == "delete" and deny["status"] == 403 and deny["actor"] == "v"


def test_audit_does_not_leak_name_out_of_scope(server):
    grant(server[1], "v", "v", "viewer", "epishu/one")
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "secret", "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    call(s, "GET", f"{API}/epishus/secret", actor="v")
    audits, _ = server[1].list("audits/")
    text = json.dumps(audits)
    assert "secret" not in text


def test_list_filters_before_pagination(server):
    grant(server[1], "late", "late", "viewer", "epishu/three", verbs=["list", "get"])
    s = server[0]
    for name in ("one", "two", "three"):
        call(s, "POST", f"{API}/epishus",
             body={"name": name, "spec": {"ziRef": "z", "suRef": "s"}}, actor="root")
    status, body = call(s, "GET", f"{API}/epishus?limit=1", actor="late")
    assert status == 200
    names = [i["metadata"]["name"] for i in body["items"]]
    assert names == ["three"]
    assert body["metadata"]["continue"] == ""
