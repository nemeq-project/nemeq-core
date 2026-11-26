from __future__ import annotations

from typing import Any

VERBS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"get", "list", "watch"}),
    "ops": frozenset({"get", "list", "watch", "create", "update", "delete"}),
    "admin_epishu": frozenset({"get", "list", "watch", "create", "update", "delete"}),
    "admin": frozenset({"*"}),
}

ROLE_POWER: dict[str, int] = {
    "viewer": 1,
    "ops": 2,
    "admin_epishu": 3,
    "admin": 4,
}

STATUS_VERB: str = "status"
STATUS_ROLES: frozenset[str] = frozenset({"ops", "admin_epishu", "admin"})


def _effective_verbs(spec: dict[str, Any]) -> frozenset[str] | None:
    role = spec.get("role")
    if role not in VERBS:
        return None
    ceiling = VERBS[role]
    raw = spec.get("verbs")
    if not isinstance(raw, list) or len(raw) == 0:
        return ceiling
    granted: set[str] = set()
    for verb in raw:
        if not isinstance(verb, str):
            continue
        if verb == "*":
            granted.update(ceiling)
            if role in STATUS_ROLES:
                granted.add(STATUS_VERB)
        elif verb == STATUS_VERB:
            if role in STATUS_ROLES:
                granted.add(STATUS_VERB)
        elif "*" in ceiling or verb in ceiling:
            granted.add(verb)
    return frozenset(granted)


def grant_verb_allows(grant: dict[str, Any] | None, verb: str) -> bool:
    if grant is None:
        return False
    try:
        spec = grant["spec"]
        if not isinstance(spec, dict):
            return False
    except (KeyError, TypeError):
        return False
    effective = _effective_verbs(spec)
    if effective is None:
        return False
    return "*" in effective or verb in effective


def grant_allows(grant: Any, verb: str, scope: str) -> bool:
    if not grant_verb_allows(grant, verb):
        return False
    try:
        spec = grant["spec"]
        if not isinstance(spec, dict):
            return False
    except (KeyError, TypeError):
        return False
    grant_scope = spec.get("scope")
    return grant_scope == "*" or grant_scope == scope


def authorize(grants: list[dict[str, Any]], verb: str, scope: str,
              *, subject: str | None = None) -> bool:
    for grant in grants:
        if subject is not None:
            try:
                if grant["spec"].get("subject") != subject:
                    continue
            except (KeyError, TypeError):
                continue
        if grant_allows(grant, verb, scope):
            return True
    return False


def scope_for(kind: str, name: str, spec: dict[str, Any]) -> str:
    if kind == "Epishu":
        return f"epishu/{name}"
    if kind == "Session":
        return f"epishu/{spec.get('epishuRef', '')}"
    if kind in ("Grant", "Quota"):
        scope_val = spec.get("scope")
        result = str(scope_val) if scope_val is not None else "default"
        return result if result else "default"
    return "cluster"


def denies_leader_change(old: dict[str, Any], new: dict[str, Any]) -> bool:
    try:
        old_spec = old["spec"]
        new_spec = new["spec"]
        return (old_spec.get("subject") != new_spec.get("subject") or
                old_spec.get("role") != new_spec.get("role"))
    except (KeyError, TypeError):
        return False
