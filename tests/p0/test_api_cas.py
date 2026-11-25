import pytest
from apiserver._testkit import API, call, server  # noqa: F401


def test_cas_conflict(server):
    s = server[0]
    status, _ = call(s, "POST", f"{API}/epishus",
                     body={"name": "o", "spec": {"ziRef": "z", "suRef": "s"}})
    assert status == 201
    status, body = call(s, "PUT", f"{API}/epishus/o",
                        body={"name": "o", "spec": {"ziRef": "z", "suRef": "s"},
                              "metadata": {"resourceVersion": "1-bogus"}})
    assert status == 409 and body["reason"] == "Conflict"


def test_cas_success(server):
    s = server[0]
    status, created = call(s, "POST", f"{API}/epishus",
                           body={"name": "o", "spec": {"ziRef": "z", "suRef": "s"}})
    assert status == 201
    rv = created["metadata"]["resourceVersion"]
    status, body = call(s, "PUT", f"{API}/epishus/o",
                        body={"name": "o", "spec": {"ziRef": "z2", "suRef": "s"},
                              "metadata": {"resourceVersion": rv}})
    assert status == 200 and body["spec"]["ziRef"] == "z2"


def test_status_subresource(server):
    s = server[0]
    call(s, "POST", f"{API}/epishus",
         body={"name": "o", "spec": {"ziRef": "z", "suRef": "s"}})
    status, body = call(s, "PUT", f"{API}/epishus/o/status",
                        body={"status": {"phase": "Ready"}})
    assert status == 200 and body["status"]["phase"] == "Ready"
    call(s, "POST", f"{API}/zis",
         body={"name": "z1", "spec": {"image": "i", "digest": "d"}})
    status, body = call(s, "PUT", f"{API}/zis/z1/status", body={"status": {}})
    assert status == 404
    assert body["reason"] == "NotFound"
