import pytest
from apiserver.server import DEFAULT_PORT, Metrics, make_server, port_from_env
from apiserver.errors import Problem
from apiserver.handlers import Api
from apiserver.openapi import build_openapi
from massaru.store import Store


def test_port_from_env(monkeypatch):
    monkeypatch.delenv("NEMEQ_API_PORT", raising=False)
    assert port_from_env() == DEFAULT_PORT == 8448
    monkeypatch.setenv("NEMEQ_API_PORT", "9001")
    assert port_from_env() == 9001
    monkeypatch.setenv("NEMEQ_API_PORT", "not-a-number")
    assert port_from_env() == DEFAULT_PORT


def test_metrics_render():
    m = Metrics()
    m.inc("nq_api_requests_total")
    m.inc("nq_api_requests_total")
    m.inc("nq_api_errors_total")
    text = m.render()
    assert "# TYPE nq_api_requests_total counter" in text
    assert "nq_api_requests_total 2" in text
    assert "nq_api_errors_total 1" in text
    assert text.endswith("\n")


def test_make_server_binds_ephemeral_port(tmp_path):
    store = Store(tmp_path / "store")
    admin = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}
    store.put("grants/admin", admin, expected_rev=0)
    api = Api(store, lambda: store.list("grants/")[0])
    srv1 = make_server(api, port=0, openapi=build_openapi())
    srv2 = make_server(api, port=0, openapi=build_openapi())
    try:
        assert srv1.openapi["openapi"] == "3.0.3"
        assert srv1.server_address[1] > 0
        assert srv2.server_address[1] > 0
        assert srv2.server_address[1] != DEFAULT_PORT
        assert srv1.server_address[1] != srv2.server_address[1]
    finally:
        srv1.server_close()
        srv2.server_close()
        store.close()


def test_handlers_list_pagination(tmp_path):
    store = Store(tmp_path / "store2")
    admin = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}
    store.put("grants/admin", admin, expected_rev=0)
    api = Api(store, lambda: store.list("grants/")[0])
    for i in range(3):
        api.create("zis", {"name": f"z{i}", "spec": {"image": "i", "digest": "d"}}, actor="root")
    page = api.list("zis", limit=2, actor="root")
    assert page["kind"] == "ZiList" and len(page["items"]) == 2
    token = page["metadata"]["continue"]
    assert token
    page2 = api.list("zis", limit=2, continue_token=token, actor="root")
    assert len(page2["items"]) == 1
    store.close()


def test_handlers_get_missing_raises_404(tmp_path):
    store = Store(tmp_path / "store3")
    admin = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}
    store.put("grants/admin", admin, expected_rev=0)
    api = Api(store, lambda: [admin])
    try:
        api.get("zis", "absent", actor="root")
    except Problem as exc:
        assert exc.status == 404 and exc.reason == "NotFound"
    else:
        assert False
    store.close()
