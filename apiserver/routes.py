from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import Problem
from .resources import RESOURCES, STATUS_SUBRESOURCE
from .errors import REASONS

API_PREFIX: str = "/apis/nemeq.io/v1alpha1"


@dataclass(frozen=True)
class Route:
    plural: str
    name: str | None
    subresource: str | None
    watch: bool
    from_rev: int
    limit: int | None
    continue_token: str | None
    dry_run: bool


def parse_path(path: str) -> tuple[str, str | None, str | None]:
    if not path.startswith(API_PREFIX + "/"):
        raise Problem(404, "NotFound", f"unknown prefix for {path!r}")
    remainder = path[len(API_PREFIX) + 1:]
    parts = [p for p in remainder.split("/") if p]
    if not parts:
        raise Problem(404, "NotFound", "empty resource path")
    if len(parts) > 3:
        raise Problem(404, "NotFound", "too many path segments")
    plural = parts[0]
    if plural not in RESOURCES:
        raise Problem(404, "NotFound", f"unknown resource {plural!r}")
    name = parts[1] if len(parts) > 1 else None
    if name is not None and name == "":
        raise Problem(404, "NotFound", "empty resource name")
    subresource = parts[2] if len(parts) > 2 else None
    if subresource is not None and subresource not in ("status",):
        raise Problem(404, "NotFound", f"unknown subresource {subresource!r}")
    return plural, name, subresource


def parse_query(query: dict[str, list[str]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "watch": False, "from_rev": 0, "limit": None,
        "continue": None, "dry_run": False,
    }
    if "watch" in query:
        vals = query["watch"]
        result["watch"] = bool(vals) and any(v in ("1", "true", "") for v in vals)
    if "from_rev" in query and "fromRev" in query:
        raise Problem(400, "BadRequest", "use fromRev or from_rev, not both")
    from_key = "fromRev" if "fromRev" in query else "from_rev"
    if from_key in query:
        raw = query[from_key][0] if query[from_key] else ""
        try:
            v = int(raw)
            if v < 0:
                raise ValueError
            result["from_rev"] = v
        except (ValueError, TypeError):
            raise Problem(400, "BadRequest", "from_rev must be int >= 0")
    if "limit" in query:
        raw = query["limit"][0] if query["limit"] else ""
        try:
            v = int(raw)
            if v < 1:
                raise ValueError
            result["limit"] = v
        except (ValueError, TypeError):
            raise Problem(400, "BadRequest", "limit must be int >= 1")
    if "continue" in query and query["continue"]:
        result["continue"] = query["continue"][0]
    if "dry_run" in query:
        vals = query["dry_run"]
        result["dry_run"] = bool(vals) and any(v in ("1", "true", "") for v in vals)
    return result


def parse_request(method: str, path: str, query: dict[str, list[str]]) -> tuple[Route, str]:
    plural, name, subresource = parse_path(path)
    q = parse_query(query)
    route = Route(plural=plural, name=name, subresource=subresource,
                  watch=q["watch"], from_rev=q["from_rev"],
                  limit=q["limit"], continue_token=q["continue"],
                  dry_run=q["dry_run"])
    if method == "GET":
        if q["watch"] and name is None:
            return route, "watch"
        if name is None:
            return route, "list"
        return route, "get"
    if method == "POST":
        if name is not None:
            raise Problem(405, "MethodNotAllowed", "POST does not take name")
        return route, "create"
    if method == "PUT":
        if name is None:
            raise Problem(405, "MethodNotAllowed", "PUT requires name")
        if subresource == "status":
            return route, "status"
        return route, "update"
    if method == "DELETE":
        if name is None:
            raise Problem(405, "MethodNotAllowed", "DELETE requires name")
        return route, "delete"
    raise Problem(405, "MethodNotAllowed", f"unsupported method {method}")


def is_status(route: Route) -> bool:
    return route.subresource == "status"
