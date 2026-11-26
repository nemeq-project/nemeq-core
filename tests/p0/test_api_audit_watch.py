import pytest
from apiserver._testkit import API, call, server  # noqa: F401


def test_readonly_denied_writes_audit(server):
    s = server[0]
    status, body = call(s, "POST", f"{API}/epishus",
                        body={"name": "bad", "spec": {"ziRef": "z", "suRef": "s", "readOnly": False}})
    assert status == 403 and body["reason"] == "Forbidden"
    audits, _ = server[1].list("audits/")
    assert len(audits) == 1
    deny = audits[0]["auditDeny"]
    assert deny["verb"] == "create" and deny["status"] == 403 and deny["kind"] == "Epishu"


def test_watch_returns_events(server):
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "o", "spec": {"ziRef": "z", "suRef": "s"}})
    status, body = call(s, "GET", f"{API}/epishus?watch=1&from_rev=0")
    assert status == 200 and isinstance(body.get("items"), list)
    assert len(body["items"]) >= 1
    first = body["items"][0]
    assert first["type"] in ("ADDED", "MODIFIED", "DELETED")
    assert all(
        item["object"] is None
        or item["object"]["kind"] == "Epishu"
        for item in body["items"]
    )


def test_watch_filters_by_kind(server):
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "e1", "spec": {"ziRef": "z", "suRef": "s"}})
    call(s, "POST", f"{API}/zis",
         body={"name": "z1", "spec": {"image": "i", "digest": "d"}})
    status, body = call(s, "GET", f"{API}/epishus?watch=1&from_rev=0")
    assert status == 200
    keys = [item["object"]["metadata"]["name"] for item in body["items"]]
    assert "e1" in keys
    assert "z1" not in keys
