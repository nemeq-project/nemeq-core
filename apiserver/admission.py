from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .resources import Reject, defaults as apply_defaults, validate_object
from .rbac import authorize, scope_for, denies_leader_change

WRITE_CAPABILITIES: frozenset[str] = frozenset({"exec", "write", "shell", "mount", "network", "sink"})
FORBIDDEN_KINDS: frozenset[str] = frozenset({"effect", "effects", "Effect"})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    status: int
    reason: str
    detail: str
    spec: dict
    audit: dict | None


def check_read_only(kind: str, spec: dict[str, Any]) -> str | None:
    if kind in FORBIDDEN_KINDS:
        return f"kind {kind!r} is forbidden"
    if spec.get("readOnly") is False:
        return f"kind {kind!r} readOnly is False"
    caps = spec.get("capabilities")
    if isinstance(caps, list):
        if set(caps) & WRITE_CAPABILITIES:
            overlap = sorted(set(caps) & WRITE_CAPABILITIES)
            return f"write capabilities not allowed: {','.join(overlap)}"
    return None


def check_quota(kind: str, spec: dict[str, Any], quotas: list[dict[str, Any]]) -> str | None:
    if kind != "Epishu":
        return None
    epishu_name = spec.get("_name", "")
    epishu_scope = f"epishu/{epishu_name}"
    for q in quotas:
        try:
            q_spec = q["spec"]
        except (KeyError, TypeError):
            continue
        if q_spec.get("scope") != epishu_scope:
            continue
        status = q.get("status") or {}
        used = status.get("used") or {}
        limit = status.get("limit") or {}
        if isinstance(used, dict) and isinstance(limit, dict):
            tokens_used = used.get("tokens", 0)
            tokens_limit = limit.get("tokens", 0)
            if tokens_limit and tokens_used >= tokens_limit:
                return f"quota exceeded for {epishu_scope}"
        return None
    return None


def deny_audit(kind: str, name: str, verb: str, actor: str, status: int, reason: str, detail: str) -> dict[str, Any]:
    return {
        "verb": verb,
        "kind": kind,
        "name": name,
        "actor": actor,
        "status": status,
        "reason": reason,
        "detail": detail,
    }


def admit(
    kind: str,
    obj: object,
    *,
    verb: str,
    grants: list[dict[str, Any]],
    quotas: list[dict[str, Any]],
    actor: str = "",
    prev: dict[str, Any] | None = None,
) -> Decision:
    try:
        validated = validate_object(kind, obj)
    except Reject as exc:
        return Decision(
            allowed=False, status=exc.status, reason=exc.reason, detail=exc.detail,
            spec={}, audit=deny_audit(kind, "", verb, actor, exc.status, exc.reason, exc.detail),
        )

    name = validated["name"]
    spec = validated["spec"]
    spec = apply_defaults(kind, spec)

    ro_reason = check_read_only(kind, spec)
    if ro_reason:
        return Decision(
            allowed=False, status=403, reason="Forbidden", detail=ro_reason,
            spec={}, audit=deny_audit(kind, name, verb, actor, 403, "Forbidden", ro_reason),
        )

    s = scope_for(kind, name, spec)
    if not authorize(grants, verb, s, subject=(actor or None)):
        return Decision(
            allowed=False, status=403, reason="Forbidden",
            detail=f"RBAC denied {verb} on {kind}/{name}",
            spec={}, audit=deny_audit(kind, name, verb, actor, 403, "Forbidden", "RBAC denied"),
        )

    quota_reason = check_quota(kind, {**spec, "_name": name}, quotas)
    if quota_reason:
        return Decision(
            allowed=False, status=429, reason="TooManyRequests", detail=quota_reason,
            spec={}, audit=deny_audit(kind, name, verb, actor, 429, "TooManyRequests", quota_reason),
        )

    if kind == "Grant" and prev is not None:
        if denies_leader_change(prev, validated):
            return Decision(
                allowed=False, status=403, reason="Forbidden", detail="leader change denied",
                spec={}, audit=deny_audit(kind, name, verb, actor, 403, "Forbidden", "leader change denied"),
            )

    spec.pop("_name", None)
    return Decision(
        allowed=True, status=200, reason="OK", detail="", spec=spec, audit=None
    )
