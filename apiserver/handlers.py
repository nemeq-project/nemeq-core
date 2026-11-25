from __future__ import annotations

from typing import Any

from .errors import Problem
from .resources import RESOURCES, STATUS_SUBRESOURCE, key_for
from .envelope import to_envelope, from_envelope, to_list, status_envelope
from .admission import admit
from .rbac import scope_for
from .authz import (
    STATUS_VERB,
    allows_collection,
    audit_target,
    require_actor,
    visible,
)
from .pagination import page_list
from .watchpage import page_events


def _resource_version(body: dict) -> str | None:
    meta = body.get("metadata", {}) if isinstance(body, dict) else {}
    rv = meta.get("resourceVersion") if isinstance(meta, dict) else None
    return rv if isinstance(rv, str) and rv else None


class Api:
    def __init__(self, store: Any, grants_provider: Any) -> None:
        self.store = store
        self.grants_provider = grants_provider
        self._deny_seq: int = 0

    def _get_kind(self, plural: str) -> str:
        if plural not in RESOURCES:
            raise Problem(404, "NotFound", f"unknown resource {plural!r}")
        return RESOURCES[plural]

    def current_rev(self) -> int:
        """Current journal revision (head) for cursor expiry checks."""
        state = getattr(self.store, "_rev", 0)
        return int(state) if isinstance(state, int) else 0

    def _grants(self) -> list[dict[str, Any]]:
        try:
            return self.grants_provider()
        except (TypeError, KeyError):
            return []

    def get(self, plural: str, name: str, actor: str = "") -> dict:
        actor = require_actor(actor)
        kind = self._get_kind(plural)
        stored = self.store.get(key_for(plural, name))
        if stored is None:
            raise Problem(404, "NotFound", f"{kind}/{name} not found")
        if not visible(self._grants(), "get", kind, name, stored.get("spec", {}), actor):
            target = audit_target(self._grants(), kind, name, stored.get("spec", {}) or {}, actor)
            self.audit_deny(
                plural, target, "get", actor, 403, "Forbidden", "not visible",
                redacted=not target,
            )
            raise Problem(403, "Forbidden", f"{kind}/{name} not visible")
        return to_envelope(kind, stored)

    def list(self, plural: str, limit: int | None = None,
             continue_token: str | None = None, actor: str = "") -> dict:
        actor = require_actor(actor)
        kind = self._get_kind(plural)
        grants = self._grants()
        if not allows_collection(grants, "list", actor):
            self.audit_deny(plural, "", "list", actor, 403, "Forbidden", "list not granted", redacted=True)
            raise Problem(403, "Forbidden", "list not granted")
        items, _ = self.store.list(f"{plural}/")
        page, next_token = page_list(grants, kind, items, actor, limit, continue_token)
        return to_list(kind, page, next_token=next_token)

    def create(self, plural: str, body: dict, actor: str = "") -> dict:
        actor = require_actor(actor)
        kind = self._get_kind(plural)
        validated = from_envelope(kind, body)
        name = validated["name"]
        decision = admit(kind, body, verb="create", grants=self._grants(),
                         quotas=[], actor=actor)
        if not decision.allowed:
            self.audit_deny(plural, name, "create", actor, decision.status, decision.reason, decision.detail)
            raise Problem(decision.status, decision.reason, decision.detail)
        stored = self.store.put(
            key_for(plural, name),
            {
                "name": name,
                "spec": decision.spec,
                "status": validated.get("status") or {},
            },
            expected_rev=0,
        )
        return to_envelope(kind, stored)

    def update(self, plural: str, name: str, body: dict, actor: str = "") -> dict:
        actor = require_actor(actor)
        kind = self._get_kind(plural)
        old = self.store.get(key_for(plural, name))
        if old is None:
            raise Problem(404, "NotFound", f"{kind}/{name} not found")
        if not visible(self._grants(), "update", kind, name, old.get("spec", {}), actor):
            self.audit_deny(plural, name, "update", actor, 403, "Forbidden", "not visible", redacted=True)
            raise Problem(403, "Forbidden", f"{kind}/{name} not visible")
        decision = admit(kind, body, verb="update", grants=self._grants(),
                         quotas=[], actor=actor, prev=old)
        if not decision.allowed:
            self.audit_deny(plural, name, "update", actor, decision.status, decision.reason, decision.detail)
            raise Problem(decision.status, decision.reason, decision.detail)
        stored = self.store.put(
            key_for(plural, name),
            {
                "name": name,
                "spec": decision.spec,
                "status": old.get("status") or {},
            },
            expected_rev=_resource_version(body),
        )
        return to_envelope(kind, stored)

    def replace_status(self, plural: str, name: str, body: dict, actor: str = "") -> dict:
        if plural not in STATUS_SUBRESOURCE:
            raise Problem(404, "NotFound", f"{plural} has no /status")
        actor = require_actor(actor)
        kind = RESOURCES[plural]
        key = key_for(plural, name)
        old = self.store.get(key)
        if old is None:
            raise Problem(404, "NotFound", f"{kind}/{name} not found")
        if not visible(self._grants(), STATUS_VERB, kind, name, old.get("spec", {}), actor):
            self.audit_deny(plural, name, STATUS_VERB, actor, 403, "Forbidden", "not visible", redacted=True)
            raise Problem(403, "Forbidden", "status not visible")
        new_status = body.get("status", {})
        stored = self.store.put(
            key,
            {
                "name": name,
                "spec": old.get("spec") or {},
                "status": new_status,
            },
            expected_rev=_resource_version(body),
        )
        return status_envelope(kind, stored)

    def delete(self, plural: str, name: str, expected_rv: str | None = None, actor: str = "") -> dict:
        actor = require_actor(actor)
        kind = self._get_kind(plural)
        old = self.store.get(key_for(plural, name))
        spec = old.get("spec", {}) if old else {}
        if not visible(self._grants(), "delete", kind, name, spec, actor):
            self.audit_deny(plural, name, "delete", actor, 403, "Forbidden", "not visible", redacted=True)
            raise Problem(403, "Forbidden", f"{kind}/{name} not visible")
        self.store.delete(key_for(plural, name), expected_rev=expected_rv, actor=actor)
        return {"status": "deleted"}

    def watch(self, plural: str, from_rev: int, limit: int = 100,
              actor: str = "") -> list[dict]:
        return self.watch_page(plural, from_rev, limit, actor)["items"]

    def watch_page(self, plural: str, from_rev: int, limit: int = 100,
                   actor: str = "") -> dict:
        actor = require_actor(actor)
        kind = self._get_kind(plural)
        grants = self._grants()
        if not allows_collection(grants, "watch", actor):
            self.audit_deny(plural, "", "watch", actor, 403, "Forbidden", "watch not granted", redacted=True)
            raise Problem(403, "Forbidden", "watch not granted")
        # Full ordered stream: page_events filters plural/scope and moves nextRev
        # across every inspected event, including other resources.
        events = list(self.store.watch(from_rev, timeout=0.05))
        return page_events(
            kind, plural, events,
            from_rev=from_rev, limit=limit, grants=grants, actor=actor,
        )

    def audit_deny(self, plural: str, name: str, verb: str, actor: str,
                   status: int, reason: str, detail: str, redacted: bool = False) -> None:
        key = f"audits/deny-{self._deny_seq}"
        self._deny_seq += 1
        kind_name = "" if redacted else name
        payload = {"auditDeny": {"verb": verb, "kind": RESOURCES.get(plural, plural),
                                  "name": kind_name, "actor": actor,
                                  "status": status, "reason": reason, "detail": detail}}
        self.store.put(key, payload)
