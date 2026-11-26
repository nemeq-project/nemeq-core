"""Shared HTTP fixture for P0 conformance tests."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from massaru.store import Store
from apiserver.handlers import Api
from apiserver.openapi import build_openapi
from apiserver.server import make_server

API: str = "/apis/nemeq.io/v1alpha1"
ADMIN: dict = {"name": "admin", "spec": {"subject": "root", "role": "admin", "scope": "*", "verbs": ["*"]}}


@pytest.fixture()
def server(tmp_path: Path):
    store = Store(tmp_path / "store")
    store.put("grants/admin", ADMIN, expected_rev=0)
    api = Api(store, lambda: store.list("grants/")[0])
    srv = make_server(api, port=0, openapi=build_openapi())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, store
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)
    store.close()


def call(
    srv,
    method: str,
    path: str,
    body: dict | None = None,
    actor: str = "root",
    raw: bytes | None = None,
) -> tuple[int, dict]:
    target = srv[0] if isinstance(srv, tuple) else srv
    url = f"http://127.0.0.1:{target.server_address[1]}{path}"
    if raw is not None:
        data: bytes | None = raw
    elif body is not None:
        data = json.dumps(body).encode()
    else:
        data = None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Nemeq-Actor": actor},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        try:
            raw_body = exc.read()
            return exc.code, json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, ValueError):
            return exc.code, {}
