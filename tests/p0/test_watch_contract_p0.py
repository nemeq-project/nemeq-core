import json
import threading
import urllib.error
import urllib.request
import pytest
from massaru.store import Store
from apiserver.handlers import Api
from apiserver.openapi import build_openapi
from apiserver.server import make_server

API = "/apis/nemeq.io/v1alpha1"


def make_srv(tmp_path, watch_buffer=1024):
    admin = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}
    store = Store(tmp_path / "store", watch_buffer=watch_buffer)
    store.put("grants/admin", admin, expected_rev=0)
    api = Api(store, lambda: store.list("grants/")[0])
    srv = make_server(api, port=0, openapi=build_openapi())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, store, thread


def http(srv, method, path, body=None, actor="root"):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "X-Nemeq-Actor": actor})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), json.loads(e.read() or b"{}")


def test_overflow_is_410_not_409(tmp_path):
    srv, store, thread = make_srv(tmp_path, watch_buffer=1)
    try:
        for i in range(2):
            store.put(f"zis/z{i}", {"name": f"z{i}", "spec": {"image": "i", "digest": "d"}},
                      expected_rev=0)
        status, ctype, body = http(srv, "GET", f"{API}/zis?watch=1&fromRev=0")
        assert status == 410
        assert ctype.startswith("application/problem+json")
        assert body["reason"] == "Expired"
        assert "relist" in body["detail"]
    finally:
        srv.shutdown(); srv.server_close(); store.close()


def test_cas_stale_is_409_not_410(tmp_path):
    srv, store, thread = make_srv(tmp_path)
    try:
        http(srv, "POST", f"{API}/epishus",
             body={"name": "o", "spec": {"ziRef": "z", "suRef": "s"}})
        status, _, body = http(srv, "PUT", f"{API}/epishus/o",
                               body={"name": "o", "metadata": {"resourceVersion": "1-bogus"},
                                     "spec": {"ziRef": "z", "suRef": "s"}})
        assert status == 409 and body["reason"] == "Conflict"
        status, _, body = http(srv, "GET", f"{API}/epishus?watch=1&fromRev=0")
        assert status == 200
    finally:
        srv.shutdown(); srv.server_close(); store.close()


def test_fromrev_and_snake_alias_same_revision(tmp_path):
    srv, store, thread = make_srv(tmp_path)
    try:
        http(srv, "POST", f"{API}/zis",
             body={"name": "z1", "spec": {"image": "i", "digest": "d"}})
        status1, _, body1 = http(srv, "GET", f"{API}/zis?watch=1&fromRev=0")
        status2, _, body2 = http(srv, "GET", f"{API}/zis?watch=1&from_rev=0")
        assert status1 == 200 and status2 == 200
        assert body1["metadata"]["nextRev"] == body2["metadata"]["nextRev"]
    finally:
        srv.shutdown(); srv.server_close(); store.close()


def test_no_ttl_cutoff_for_old_fromrev(tmp_path):
    srv, store, thread = make_srv(tmp_path)
    try:
        for i in range(3):
            store.put(f"zis/z{i}", {"name": f"z{i}", "spec": {"image": "i", "digest": "d"}},
                      expected_rev=0)
        status, _, body = http(srv, "GET", f"{API}/zis?watch=1&fromRev=0")
        assert status == 200
        assert body["metadata"]["nextRev"] >= 3
    finally:
        srv.shutdown(); srv.server_close(); store.close()


def test_both_spellings_conflict_is_400(tmp_path):
    srv, store, thread = make_srv(tmp_path)
    try:
        status, _, body = http(srv, "GET", f"{API}/zis?watch=1&fromRev=0&from_rev=0")
        assert status == 400 and body["reason"] == "BadRequest"
    finally:
        srv.shutdown(); srv.server_close(); store.close()
