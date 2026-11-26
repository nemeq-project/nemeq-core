from __future__ import annotations

import copy
import re
from typing import Any

API_VERSION: str = "nemeq.io/v1alpha1"

RESOURCES: dict[str, str] = {
    "zis": "Zi",
    "sus": "Su",
    "epishus": "Epishu",
    "sessions": "Session",
    "grants": "Grant",
    "quotas": "Quota",
    "models": "Model",
}

PLURALS: dict[str, str] = {v: k for k, v in RESOURCES.items()}

STATUS_SUBRESOURCE: frozenset[str] = frozenset({"epishus", "sessions", "quotas"})

REQUIRED_SPEC: dict[str, tuple[str, ...]] = {
    "Zi": ("image", "digest"),
    "Su": ("image", "digest"),
    "Epishu": ("ziRef", "suRef"),
    "Session": ("epishuRef", "principal"),
    "Grant": ("subject", "role", "scope", "verbs"),
    "Quota": ("scope",),
    "Model": ("provider", "modelId"),
}

GRANT_ROLES: frozenset[str] = frozenset({"admin", "admin_epishu", "ops", "viewer"})


class Reject(Exception):
    def __init__(self, status: int, reason: str, detail: str) -> None:
        self.status: int = status
        self.reason: str = reason
        self.detail: str = detail


def key_for(plural: str, name: str) -> str:
    return f"{plural}/{name}".lower()


_NAME_RE: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,62}$")


def validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise Reject(422, "Invalid", f"bad name: {name!r}")


def defaults(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = copy.deepcopy(spec)
    if kind == "Epishu":
        out.setdefault("readOnly", True)
        out.setdefault("kind", "awilu")
        out.setdefault("quotas", {})
    elif kind == "Su":
        out.setdefault("endpointClass", "default")
        out.setdefault("capabilities", [])
    elif kind == "Zi":
        out.setdefault("manifest", {})
    elif kind == "Session":
        out.setdefault("priority", 0)
        out.setdefault("budget", {})
    elif kind == "Quota":
        out.setdefault("tokens", 0)
        out.setdefault("cost", 0.0)
        out.setdefault("concurrency", 1)
    elif kind == "Model":
        out.setdefault("context", 0)
        out.setdefault("price", 0.0)
    return out


def validate_spec(kind: str, spec: object) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise Reject(422, "Invalid", "spec must be object")

    result: dict[str, Any] = {}
    required = REQUIRED_SPEC.get(kind, ())

    for field in required:
        value = spec.get(field)
        if field == "verbs":
            if not isinstance(value, list) or len(value) == 0:
                raise Reject(422, "Invalid", f"spec.{field} must be non-empty list[str]")
            continue
        if not isinstance(value, str) or not value:
            raise Reject(422, "Invalid", f"spec.{field} must be non-empty string")

    for field, value in spec.items():
        if field in ("readOnly",):
            if not isinstance(value, bool):
                raise Reject(422, "Invalid", f"spec.{field} must be bool")
        elif field in ("tokens",):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise Reject(422, "Invalid", f"spec.{field} must be int >= 0")
        elif field in ("cost",):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise Reject(422, "Invalid", f"spec.{field} must be number >= 0")
        elif field in ("concurrency",):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise Reject(422, "Invalid", f"spec.{field} must be int >= 1")
        elif field in ("context",):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise Reject(422, "Invalid", f"spec.{field} must be int >= 0")
        result[field] = value

    return result


def validate_object(kind: str, obj: object) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise Reject(422, "Invalid", "object must be dict")
    if "name" not in obj or "spec" not in obj:
        raise Reject(422, "Invalid", "object must have name and spec")
    validate_name(obj["name"])
    return {"name": obj["name"], "spec": validate_spec(kind, obj["spec"])}
