from __future__ import annotations

import json
from typing import Any, Tuple

from .client import Client, ApiError

PLURAL: dict[str, str] = {
    "zi": "zis", "su": "sus", "epishu": "epishus",
    "session": "sessions", "grant": "grants",
    "quota": "quotas", "model": "models",
}

_KIND_MAP: dict[str, str] = {
    "zis": "Zi", "sus": "Su", "epishus": "Epishu",
    "sessions": "Session", "grants": "Grant",
    "quotas": "Quota", "models": "Model",
}


def plural_of(kind: str) -> str:
    if kind in PLURAL:
        return PLURAL[kind]
    if kind in _KIND_MAP:
        return kind
    raise ValueError(f"unknown kind: {kind!r}")


def _kind_name(plural: str) -> str:
    return _KIND_MAP.get(plural, plural.capitalize())


def cmd_health(client: Client) -> Tuple[int, str]:
    try:
        result = client.health()
        return 0, json.dumps(result, sort_keys=True)
    except ApiError as exc:
        return render_error(exc)


def cmd_get(client: Client, kind: str, name: str | None = None) -> Tuple[int, str]:
    try:
        plural = plural_of(kind)
        if name is None:
            result = client.list(plural)
            items = result.get("items", [])
            return 0, "\n".join(json.dumps(obj, sort_keys=True) for obj in items)
        result = client.get(plural, name)
        return 0, json.dumps(result, sort_keys=True)
    except ApiError as exc:
        return render_error(exc)
    except ValueError as exc:
        return 400, json.dumps({"title": "Invalid", "detail": str(exc)}, sort_keys=True)


def cmd_describe(client: Client, kind: str, name: str) -> Tuple[int, str]:
    try:
        plural = plural_of(kind)
        result = client.get(plural, name)
        meta = result.get("metadata", {})
        spec = result.get("spec", {})
        status = result.get("status", {})
        lines = [
            f"Name: {meta.get('name', '')}",
            f"Kind: {_kind_name(plural)}",
            f"ResourceVersion: {meta.get('resourceVersion', '')}",
            f"Generation: {meta.get('generation', '')}",
            "",
            "Spec:",
            json.dumps(spec, sort_keys=True, indent=2),
            "",
            "Status:",
            json.dumps(status, sort_keys=True, indent=2),
        ]
        return 0, "\n".join(lines)
    except ApiError as exc:
        return render_error(exc)
    except ValueError as exc:
        return 400, json.dumps({"title": "Invalid", "detail": str(exc)}, sort_keys=True)


def cmd_apply(client: Client, manifest: str) -> Tuple[int, str]:
    try:
        body = json.loads(manifest)
    except ValueError as exc:
        return 400, json.dumps({"title": "Invalid", "detail": str(exc)}, sort_keys=True)
    if not isinstance(body, dict):
        return 400, json.dumps({"title": "Invalid", "detail": "manifest must be object"}, sort_keys=True)
    kind = body.get("kind", "")
    try:
        plural = plural_of(kind)
    except ValueError as exc:
        return 400, json.dumps({"title": "Invalid", "detail": str(exc)}, sort_keys=True)
    try:
        result = client.create(plural, body)
        return 201, json.dumps(result, sort_keys=True)
    except ApiError as exc:
        if exc.status == 409:
            try:
                name = (body.get("metadata") or {}).get("name", "")
                existing = client.get(plural, name)
                rv = existing.get("metadata", {}).get("resourceVersion", "")
                body["metadata"]["resourceVersion"] = rv
                result = client.replace(plural, name, body)
                return 200, json.dumps(result, sort_keys=True)
            except ApiError as e:
                return render_error(e)
        return render_error(exc)


def cmd_watch(client: Client, kind: str, from_rev: int = 0, limit: int = 100) -> Tuple[int, str]:
    try:
        plural = plural_of(kind)
        result = client.watch(plural, from_rev=from_rev, limit=limit)
        items = result.get("items", [])
        return 0, "\n".join(json.dumps(obj, sort_keys=True) for obj in items)
    except ApiError as exc:
        return render_error(exc)
    except ValueError as exc:
        return 400, json.dumps({"title": "Invalid", "detail": str(exc)}, sort_keys=True)


def render_error(exc: ApiError) -> Tuple[int, str]:
    return (exc.status, json.dumps(exc.problem, sort_keys=True))
