import json
import threading
import urllib.error
import urllib.request


def make_srv(tmp_path, watch_buffer):
    from massaru.store import Store
    from apiserver.handlers import Api
    from apiserver.openapi import build_openapi
    from apiserver.server import make_server
    admin = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}
    store = Store(tmp_path / "store", watch_buffer=watch_buffer)
    store.put("grants/admin", admin, expected_rev=0)
    api = Api(store, lambda: store.list("grants/")[0])
    srv = make_server(api, port=0, openapi=build_openapi())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, store, thread


def raw_get(srv, path, actor="root"):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    req = urllib.request.Request(url, headers={"X-Nemeq-Actor": actor})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), json.loads(exc.read() or b"{}")


def test_watch_overflow_http_410_canonical_fromrev(tmp_path):
    srv, store, thread = make_srv(tmp_path, watch_buffer=1)
    try:
        for i in range(2):
            store.put(f"zis/z{i}", {"name": f"z{i}", "spec": {"image": "i", "digest": "d"}}, expected_rev=0)
        status, ctype, body = raw_get(srv, "/apis/nemeq.io/v1alpha1/zis?watch=1&fromRev=0")
        assert status == 410
        assert ctype.startswith("application/problem+json")
        assert body["status"] == 410 and body["reason"] == "Expired"
        assert "relist" in body["detail"]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        store.close()


def test_watch_overflow_http_410_snake_alias(tmp_path):
    srv, store, thread = make_srv(tmp_path, watch_buffer=1)
    try:
        for i in range(2):
            store.put(f"zis/z{i}", {"name": f"z{i}", "spec": {"image": "i", "digest": "d"}}, expected_rev=0)
        status, _, body = raw_get(srv, "/apis/nemeq.io/v1alpha1/zis?watch=1&from_rev=0")
        assert status == 410 and body["reason"] == "Expired"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        store.close()


def test_watch_valid_cursor_not_410(tmp_path):
    srv, store, thread = make_srv(tmp_path, watch_buffer=32768)
    try:
        store.put("zis/z1", {"name": "z1", "spec": {"image": "i", "digest": "d"}}, expected_rev=0)
        status, _, body = raw_get(srv, "/apis/nemeq.io/v1alpha1/zis?watch=1&fromRev=0")
        assert status == 200 and "nextRev" in body["metadata"]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        store.close()


def test_watch_fromrev_both_spellings_conflict(tmp_path):
    srv, store, thread = make_srv(tmp_path, watch_buffer=1024)
    try:
        status, _, body = raw_get(srv, "/apis/nemeq.io/v1alpha1/zis?watch=1&fromRev=0&from_rev=0")
        assert status == 400 and body["reason"] == "BadRequest"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        store.close()
