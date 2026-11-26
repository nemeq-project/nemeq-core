import pytest
from apiserver._testkit import API, call, server  # noqa: F401


def test_from_rev_alias(server):
    from apiserver.routes import parse_request, API_PREFIX
    route, verb = parse_request("GET", API_PREFIX + "/zis",
                                {"watch": ["1"], "fromRev": ["7"]})
    assert verb == "watch" and route.from_rev == 7
    route2, _ = parse_request("GET", API_PREFIX + "/zis",
                              {"watch": ["1"], "from_rev": ["5"]})
    assert route2.from_rev == 5
    s = server[0]
    status, body = call(s, "GET", f"{API}/zis?watch=1&fromRev=0")
    assert status == 200 and "metadata" in body


def test_from_rev_conflict_400():
    from apiserver.routes import parse_request, API_PREFIX
    from apiserver.errors import Problem
    with pytest.raises(Problem) as exc:
        parse_request("GET", API_PREFIX + "/zis",
                      {"watch": ["1"], "fromRev": ["1"], "from_rev": ["2"]})
    assert exc.value.status == 400


def test_watch_overflow_returns_410(tmp_path):
    import threading, json, urllib.error, urllib.request
    from massaru.store import Store
    from apiserver.handlers import Api
    from apiserver.openapi import build_openapi
    from apiserver.server import make_server
    admin = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}
    store = Store(tmp_path / "store", watch_buffer=2)
    store.put("grants/admin", admin, expected_rev=0)
    api = Api(store, lambda: store.list("grants/")[0])
    srv = make_server(api, port=0, openapi=build_openapi())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        for i in range(3):
            store.put(f"zis/z{i}", {"name": f"z{i}", "spec": {"image": "i", "digest": "d"}}, expected_rev=0)
        url = f"http://127.0.0.1:{srv.server_address[1]}/apis/nemeq.io/v1alpha1/zis?watch=1&from_rev=0"
        req = urllib.request.Request(url, headers={"X-Nemeq-Actor": "root"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = json.loads(exc.read())
        assert status == 410 and body["reason"] == "Expired"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
        store.close()
