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
        return str(grant["spec"].get("subject") or "")
    except (KeyError, TypeError):
        return ""


def _grants_for(grants: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
    return [g for g in grants if subject_of(g) == actor]


def allows_collection(grants: list[dict], verb: str, actor: str) -> bool:
    if not actor or verb not in ALL_VERBS:
        return False
    return any(grant_verb_allows(g, verb) for g in _grants_for(grants, actor))


def has_any_grant(grants: list[dict], actor: str) -> bool:
    return bool(actor) and bool(_grants_for(grants, actor))


def allowed(grants: list[dict], verb: str, scope: str, actor: str) -> bool:
    if not actor or verb not in ALL_VERBS:
        return False
    return any(grant_allows(g, verb, scope) for g in _grants_for(grants, actor))


def visible(grants: list[dict], verb: str, kind: str, name: str,
            spec: dict[str, Any], actor: str) -> bool:
    return allowed(grants, verb, scope_for(kind, name, spec), actor)


def audit_target(grants: list[dict], kind: str, name: str, spec: dict[str, Any],
                 actor: str) -> str:
    scope = scope_for(kind, name, spec)
    for g in _grants_for(grants, actor):
        for verb in ALL_VERBS:
            if grant_allows(g, verb, scope):
                return name
    return ""


def filter_visible(grants: list[dict], verb: str, kind: str,
                   items: list[dict], actor: str) -> list[dict]:
    return [i for i in items
            if visible(grants, verb, kind, i["name"], i.get("spec") or {}, actor)]


def enforcement_note() -> str:
    return "rbac: actor required; verbs=" + ",".join(sorted(ALL_VERBS))
