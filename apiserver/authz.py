from __future__ import annotations

from typing import Any

from .rbac import grant_allows, grant_verb_allows, scope_for
from .errors import Problem

STATUS_VERB: str = "status"
ALL_VERBS: frozenset[str] = frozenset({"get", "list", "watch", "create", "update", "delete", "status"})


def require_actor(actor: str | None) -> str:
    if not actor or not actor.strip():
        raise Problem(403, "Forbidden", "actor is required")
    return actor.strip()


def subject_of(grant: dict[str, Any]) -> str:
    try:
        return grant["spec"].get("subject", "") or ""
    except (KeyError, TypeError):
        return ""


def _grants_for(grants: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
    return [g for g in grants if subject_of(g) == actor]


def allows_collection(grants: list[dict[str, Any]], verb: str, actor: str) -> bool:
    """Collection gate: actor has a Grant granting this verb anywhere.

    Item/event scope filtering happens after this gate, so a scoped Grant
    still only yields its own objects, while a get-only actor is denied.
    """
    if not actor or verb not in ALL_VERBS:
        return False
    return any(grant_verb_allows(g, verb) for g in _grants_for(grants, actor))


def has_any_grant(grants: list[dict[str, Any]], actor: str) -> bool:
    """True when the actor has at least one Grant object of its own."""
    if not actor:
        return False
    return bool(_grants_for(grants, actor))


def allowed(grants: list[dict[str, Any]], verb: str, scope: str, actor: str) -> bool:
    if verb not in ALL_VERBS:
        return False
    if not actor:
        return False
    return any(grant_allows(g, verb, scope) for g in _grants_for(grants, actor))


def visible(grants: list[dict[str, Any]], verb: str, kind: str, name: str,
            spec: dict[str, Any], actor: str) -> bool:
    scope = scope_for(kind, name, spec)
    return allowed(grants, verb, scope, actor)


def audit_target(grants: list[dict[str, Any]], kind: str, name: str,
                 spec: dict[str, Any], actor: str) -> str:
    scope = scope_for(kind, name, spec)
    for g in _grants_for(grants, actor):
        for v in ALL_VERBS:
            if grant_allows(g, v, scope):
                return name
    return ""


def filter_visible(grants: list[dict[str, Any]], verb: str, kind: str,
                   items: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
    return [item for item in items if visible(grants, verb, kind, item["name"], item["spec"], actor)]


def enforcement_note() -> str:
    verbs = ",".join(sorted(ALL_VERBS))
    return f"rbac: actor required; verbs={verbs}"
