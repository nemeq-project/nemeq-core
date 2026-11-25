from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from .errors import Problem, problem, from_store_error, CONTENT_TYPE
from .routes import parse_request, Route
from .handlers import Api


DEFAULT_PORT: int = 8448


def port_from_env() -> int:
    try:
        return int(os.environ.get("NEMEQ_API_PORT", str(DEFAULT_PORT)))
    except (ValueError, TypeError):
        return DEFAULT_PORT


class Metrics:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + value

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            for name in sorted(self._counts):
                lines.append(f"# TYPE {name} counter\n")
                lines.append(f"{name} {self._counts[name]}\n")
        return "".join(lines)


class Handler(BaseHTTPRequestHandler):
    api: Api | None = None
    metrics: Metrics | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_problem(self, p: Problem) -> None:
        body = json.dumps(p.to_dict()).encode()
        self.send_response(p.status)
        self.send_header("Content-Type", CONTENT_TYPE)
        if p.headers:
            for k, v in p.headers.items():
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        path, _, raw_query = self.path.partition("?")
        query = parse_qs(raw_query, keep_blank_values=True)
        if path == "/healthz":
            return self._send_json(200, {"status": "ok"})
        if path == "/openapi.json":
            return self._send_json(200, self.server.openapi)
        if path == "/metrics":
            body = (self.metrics.render() if self.metrics else "").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            route, verb = parse_request(method, path, query)
        except Problem as p:
            return self._send_problem(p)
        actor = self.headers.get("X-Nemeq-Actor", "")
        body: dict = {}
        if method in ("POST", "PUT"):
            length = int(self.headers.get("Content-Length", 0))
            if length > 2 * 1024 * 1024:
                return self._send_problem(problem(413, "body exceeds 2 MiB", "TooLarge"))
            raw = self.rfile.read(length) if length else b""
            if raw:
                try:
                    body = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    return self._send_problem(problem(400, "invalid JSON", "BadRequest"))
        api = self.api
        if api is None:
            return self._send_problem(problem(503, "no api bound", "Unavailable"))
        try:
            if verb == "get":
                result = api.get(route.plural, route.name, actor=actor)
            elif verb == "list":
                result = api.list(
                    route.plural,
                    limit=route.limit,
                    continue_token=route.continue_token,
                    actor=actor,
                )
            elif verb == "watch":
                payload = api.watch_page(
                    route.plural,
                    from_rev=route.from_rev,
                    limit=route.limit or 100,
                    actor=actor,
                )
                if self.metrics is not None:
                    self.metrics.inc("nq_api_requests_total")
                return self._send_json(200, payload)
            elif verb == "create":
                result = api.create(route.plural, body, actor=actor)
            elif verb == "update":
                result = api.update(route.plural, route.name, body, actor=actor)
            elif verb == "status":
                result = api.replace_status(route.plural, route.name, body, actor=actor)
            elif verb == "delete":
                rv = query.get("expectedRv", [None])[0] or query.get("expected_rv", [None])[0]
                result = api.delete(route.plural, route.name, expected_rv=rv, actor=actor)
            else:
                return self._send_problem(problem(405, "unknown verb", "MethodNotAllowed"))
        except Problem as p:
            return self._send_problem(p)
        except Exception as exc:
            return self._send_problem(from_store_error(exc))
        status = 201 if verb == "create" else 200
        if self.metrics is not None:
            self.metrics.inc("nq_api_requests_total")
            if status >= 400:
                self.metrics.inc("nq_api_errors_total")
        self._send_json(status, result)


class Server(ThreadingHTTPServer):
    daemon_threads: bool = True
    openapi: dict = {}


def make_server(api: Api, host: str = "127.0.0.1",
                port: int | None = None, openapi: dict | None = None) -> Server:
    p = DEFAULT_PORT if port is None else port
    srv = Server((host, p), Handler)
    srv.openapi = openapi or {}
    Handler.api = api
    Handler.metrics = Metrics()
    return srv


def serve(api: Api, host: str | None = None, port: int | None = None,
          openapi: dict | None = None) -> None:
    h = host or os.environ.get("NEMEQ_API_HOST", "127.0.0.1")
    p = port_from_env() if port is None else port
    srv = make_server(api, host=h, port=p, openapi=openapi)
    print(f"listening on http://{h}:{p}", flush=True)
    srv.serve_forever()
