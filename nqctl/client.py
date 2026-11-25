from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL: str = "http://127.0.0.1:8448"


class ApiError(Exception):
    def __init__(self, status: int, problem: dict[str, Any]) -> None:
        self.status: int = status
        self.problem: dict[str, Any] = problem

    def __str__(self) -> str:
        return self.problem.get("detail") or self.problem.get("title") or f"HTTP {self.status}"


def load_config(path: str | None = None) -> dict[str, Any]:
    if path is None:
        path = str(Path.home() / ".nq" / "config")
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def resolve_url(explicit: str | None = None, config: dict | None = None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("NEMEQ_API_URL")
    if env:
        return env.rstrip("/")
    if config and config.get("url"):
        return config["url"].rstrip("/")
    return DEFAULT_URL


def resolve_actor(explicit: str | None = None, config: dict | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("NEMEQ_ACTOR")
    if env:
        return env
    if config and config.get("actor"):
        return config["actor"]
    return ""


def _build_url(base: str, path: str, params: dict[str, Any]) -> str:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return f"{base}{path}?{qs}" if qs else f"{base}{path}"


def _do_request(url: str, method: str, body: dict | None, actor: str, timeout: float) -> dict:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if actor:
        headers["X-Nemeq-Actor"] = actor
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            problem = json.loads(exc.read())
        except (json.JSONDecodeError, ValueError):
            problem = {"title": exc.reason, "detail": str(exc)}
        raise ApiError(exc.code, problem) from exc
    except urllib.error.URLError as exc:
        raise ApiError(0, {"title": "connection failed", "detail": str(exc)}) from exc


class Client:
    def __init__(self, url: str, actor: str = "", timeout: float = 10.0) -> None:
        self.url: str = url.rstrip("/")
        self.actor: str = actor
        self.timeout: float = timeout

    def _path(self, plural: str, name: str | None = None, subresource: str = "") -> str:
        base = f"/apis/nemeq.io/v1alpha1/{plural}"
        if name:
            base += f"/{name}"
        if subresource:
            base += f"/{subresource}"
        return base

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.url}{path}"
        return _do_request(url, method, body, self.actor, self.timeout)

    def _q(self, **kwargs: Any) -> dict[str, Any]:
        return {k: v for k, v in kwargs.items() if v is not None}

    def get(self, plural: str, name: str) -> dict:
        return self.request("GET", self._path(plural, name))

    def list(self, plural: str, limit: int | None = None,
             continue_token: str | None = None) -> dict:
        qs = self._q(limit=limit, continue_=continue_token)
        if continue_token is not None:
            qs["continue"] = continue_token
            qs.pop("continue_", None)
        return self.request("GET", _build_url("", self._path(plural), qs))

    def create(self, plural: str, body: dict) -> dict:
        return self.request("POST", self._path(plural), body=body)

    def replace(self, plural: str, name: str, body: dict) -> dict:
        return self.request("PUT", self._path(plural, name), body=body)

    def replace_status(self, plural: str, name: str, body: dict) -> dict:
        return self.request("PUT", self._path(plural, name, subresource="status"), body=body)

    def delete(self, plural: str, name: str, expected_rv: str | None = None) -> dict:
        qs = self._q(expectedRv=expected_rv)
        url = _build_url(self.url, self._path(plural, name), qs)
        return _do_request(url, "DELETE", None, self.actor, self.timeout)

    def watch(self, plural: str, from_rev: int = 0, limit: int | None = None) -> dict:
        qs = self._q(watch="1", from_rev=from_rev, limit=limit)
        url = _build_url(self.url, self._path(plural), qs)
        return _do_request(url, "GET", None, self.actor, self.timeout)

    def health(self) -> dict:
        return self.request("GET", "/healthz")

    def openapi(self) -> dict:
        return self.request("GET", "/openapi.json")

    def health(self) -> dict:
        return self.request("GET", "/healthz")

    def openapi(self) -> dict:
        return self.request("GET", "/openapi.json")
