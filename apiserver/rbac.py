from __future__ import annotations

from typing import Any

VERBS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"get", "list", "watch"}),
    "ops": frozenset({"get", "list", "watch", "create", "update", "delete"}),
    "admin_epishu": frozenset({"get", "list", "watch", "create", "update", "delete", "adminscope"}),
    "admin": frozenset({"*"}),
}

ROLE_POWER: dict[str, int] = {
    "viewer": 1,
    "ops": 2,
    "admin_epishu": 3,
    "admin": 4,
}


def _check_grant(grant: dict[str, Any], verb: str, scope: str) -> bool:
    try:
        spec = grant["spec"]
    except (KeyError, TypeError):
        return False

    role = spec.get("role")
    if role not in VERBS:
        return False

    user_verbs = spec.get("verbs", [])
    if not isinstance(user_verbs, list):
        user_verbs = []

    role_verbs = VERBS.get(role, frozenset())
    if "*" in role_verbs or "*" in user_verbs:
        verb_allowed = True
    else:
        verb_allowed = verb in role_verbs or verb in user_verbs

    grant_scope = spec.get("scope")
    scope_ok = grant_scope == "*" or grant_scope == scope

    return verb_allowed and scope_ok


def grant_allows(grant: dict[str, Any] | None, verb: str, scope: str) -> bool:
    if grant is None:
        return False
    return _check_grant(grant, verb, scope)


def authorize(
    grants: list[dict[str, Any]],
    verb: str,
    scope: str,
    *,
    subject: str | None = None,
) -> bool:
    for grant in grants:
        if subject is not None:
            try:
                if grant["spec"].get("subject") != subject:
                    continue
            except (KeyError, TypeError):
                continue
        if _check_grant(grant, verb, scope):
            return True
    return False


def scope_for(kind: str, name: str, spec: dict[str, Any]) -> str:
    if kind == "Epishu":
        return f"epishu/{name}"
    if kind == "Session":
        return f"epishu/{spec.get('epishuRef', '')}"
    if kind in ("Grant", "Quota"):
        return str(spec.get("scope", "default")) or "default"
    return "cluster"


def denies_leader_change(old: dict[str, Any], new: dict[str, Any]) -> bool:
    try:
        old_spec = old["spec"]
        new_spec = new["spec"]
    except (KeyError, TypeError):
        return False
    return (
        old_spec.get("subject") != new_spec.get("subject")
        or old_spec.get("role") != new_spec.get("role")
    )
