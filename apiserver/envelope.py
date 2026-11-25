from __future__ import annotations

from typing import Any

API_VERSION: str = "nemeq.io/v1alpha1"


def to_envelope(kind: str, stored: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "name": stored.get("name", ""),
        "uid": stored.get("uid", ""),
        "resourceVersion": stored.get("resourceVersion", ""),
        "generation": int(stored.get("generation", 0)),
    }
    envelope = {
        "apiVersion": API_VERSION,
        "kind": kind,
        "metadata": metadata,
        "spec": dict(stored.get("spec", {})),
    }
    status = stored.get("status")
    if status is not None:
        envelope["status"] = dict(status) if isinstance(status, dict) else {}
    else:
        envelope["status"] = {}
    return envelope


def from_envelope(kind: str, body: object) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("body must be a dict")

    name = None
    spec: dict[str, Any] = {}
    status: dict[str, Any] = {}

    if "metadata" in body and isinstance(body["metadata"], dict):
        name = body["metadata"].get("name")
        if kind != body.get("kind", kind):
            raise ValueError(
                f"kind mismatch: expected {kind!r}, got {body.get('kind')!r}"
            )
        spec = body.get("spec", {})
        status = body.get("status", {})
    elif "name" in body:
        name = body.get("name")
        if kind != body.get("kind", kind):
            raise ValueError(
                f"kind mismatch: expected {kind!r}, got {body.get('kind')!r}"
            )
        spec = body.get("spec", {})
        status = body.get("status", {})

    if name is None:
        raise ValueError("missing name in body")

    return {"name": name, "spec": spec, "status": status}


def to_list(
    kind: str, items: list[dict[str, Any]], next_token: str | None = None
) -> dict[str, Any]:
    return {
        "apiVersion": API_VERSION,
        "kind": f"{kind}List",
        "metadata": {"continue": next_token or ""},
        "items": [to_envelope(kind, it) for it in items],
    }


def status_envelope(kind: str, stored: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": API_VERSION,
        "kind": kind,
        "metadata": {
            "name": stored.get("name", ""),
            "uid": stored.get("uid", ""),
            "resourceVersion": stored.get("resourceVersion", ""),
            "generation": int(stored.get("generation", 0)),
        },
        "status": stored.get("status") or {},
    }
