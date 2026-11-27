import json
import urllib.error
import urllib.request
import pytest
from apiserver._testkit import ADMIN, API, call, server  # noqa: F401


def test_healthz(server):
    """§9.3: сервер без привязанного инстанса обязан отличаться от здорового.

    Раньше здесь ждалось `{"status": "ok"}` — то есть «инстанса нет» было
    неотличимо от «инстанс здоров». Теперь маркер явный; расширенный ответ
    с `rev`/`last_hash` даёт только сервер, привязанный к инстансу.
    """
    status, body = call(server, "GET", "/healthz")
    assert status == 200
    assert body["status"] == "no-instance"
    assert "rev" not in body


def test_openapi(server):
    status, body = call(server, "GET", "/openapi.json")
    assert status == 200
    assert body["openapi"] == "3.0.3"
    assert len(body["paths"]) == 17
    assert f"{API}/epishus/{{name}}/status" in body["paths"]
    assert f"{API}/zis" in body["paths"]


def test_metrics(server):
    srv = server[0]
    url = f"http://127.0.0.1:{srv.server_address[1]}/metrics"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/plain")
        resp.read()


def test_create_get_list_delete(server):
    s = server[0]
    epishu = {"name": "octavia", "spec": {"ziRef": "z", "suRef": "s"}}
    status, created = call(s, "POST", f"{API}/epishus", body=epishu)
    assert status == 201
    assert len(created["metadata"]["resourceVersion"]) > 0
    assert created["spec"]["readOnly"] is True
    status, got = call(s, "GET", f"{API}/epishus/octavia")
    assert status == 200
    status, lst = call(s, "GET", f"{API}/epishus")
    assert status == 200 and lst["kind"] == "EpishuList"
    assert len(lst["items"]) == 1
    status, deleted = call(s, "DELETE", f"{API}/epishus/octavia")
    assert status == 200 and deleted["status"] == "deleted"
    status, err = call(s, "GET", f"{API}/epishus/octavia")
    assert status == 404 and err["reason"] == "NotFound"


def test_unknown_resource(server):
    status, body = call(server, "GET", f"{API}/nope")
    assert status == 404 and body["reason"] == "NotFound"


def test_invalid_json(server):
    s = server[0]
    url = f"http://127.0.0.1:{s.server_address[1]}{API}/epishus"
    req = urllib.request.Request(url, data=b"{not json", method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Nemeq-Actor": "root"})
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        body = json.loads(exc.read())
        assert body["reason"] == "BadRequest"
