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


STATUS_VERB: str = "status"
# `status` is not part of the role ceiling for viewer; only these roles may
# ever hold it, and only when explicitly listed in spec.verbs.
STATUS_ROLES: frozenset[str] = frozenset({"ops", "admin_epishu", "admin"})


def _effective_verbs(spec: dict[str, Any]) -> frozenset[str] | None:
    """Effective verb set, or None when spec is unusable.

    Role verbs are the ceiling; an explicit non-empty ``spec.verbs`` narrows
    them and can never escalate. ``status`` is special: it is granted only to
    ops/admin roles and only when explicitly listed.
    """
    role = spec.get("role")
    if role not in VERBS:
        return None
    ceiling = VERBS[role]
    raw = spec.get("verbs")
    if not isinstance(raw, list) or not raw:
        # Empty/absent list keeps role defaults (backwards compatible).
        return ceiling
    granted: set[str] = set()
    for verb in raw:
        if not isinstance(verb, str):
            continue
        if verb == "*":
            granted.update(ceiling)
            if role in STATUS_ROLES:
                granted.add(STATUS_VERB)
            continue
        if verb == STATUS_VERB:
            if role in STATUS_ROLES:
                granted.add(STATUS_VERB)
            continue
        if "*" in ceiling or verb in ceiling:
            granted.add(verb)
    return frozenset(granted)


def grant_verb_allows(grant: dict[str, Any] | None, verb: str) -> bool:
    """Verb capability of a grant, ignoring scope (collection gate)."""
    if grant is None:
        return False
    try:
        spec = grant["spec"]
    except (KeyError, TypeError):
        return False
    if not isinstance(spec, dict):
        return False
    effective = _effective_verbs(spec)
    if effective is None:
        return False
    return "*" in effective or verb in effective


def grant_allows(grant: dict[str, Any] | None, verb: str, scope: str) -> bool:
    if grant is None:
        return False
    try:
        spec = grant["spec"]
    except (KeyError, TypeError):
        return False
    if not isinstance(spec, dict):
        return False
    if not grant_verb_allows(grant, verb):
        return False
    grant_scope = spec.get("scope")
    return grant_scope == "*" or grant_scope == scope


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
        if grant_allows(grant, verb, scope):
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
