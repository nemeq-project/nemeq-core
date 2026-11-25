from __future__ import annotations

from typing import Any

from .errors import problem, Problem
from .resources import RESOURCES, STATUS_SUBRESOURCE, key_for
from .envelope import to_envelope, from_envelope, to_list, status_envelope
from .admission import admit
from .rbac import scope_for, authorize


def _resource_version(body: dict) -> str | None:
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("resourceVersion")
        if isinstance(value, str) and value:
            return value
    value = body.get("resourceVersion")
    if isinstance(value, str) and value:
        return value
    return None


class Api:
    def __init__(self, store: Any, grants_provider: Any) -> None:
        self.store = store
        self.grants_provider = grants_provider
        self._deny_seq: int = 0

    def _get_kind(self, plural: str) -> str:
        if plural not in RESOURCES:
            raise Problem(404, "NotFound", f"unknown resource {plural!r}")
        return RESOURCES[plural]

    def get(self, plural: str, name: str) -> dict:
        kind = self._get_kind(plural)
        stored = self.store.get(key_for(plural, name))
        if stored is None:
            raise Problem(404, "NotFound", f"{kind}/{name} not found")
        return to_envelope(kind, stored)

    def list(self, plural: str, limit: int | None = None,
             continue_token: str | None = None) -> dict:
        kind = self._get_kind(plural)
        items, nxt = self.store.list(f"{plural}/", limit=limit, continue_token=continue_token)
        return to_list(kind, items, next_token=nxt)

    def create(self, plural: str, body: dict, actor: str = "") -> dict:
        kind = self._get_kind(plural)
        validated = from_envelope(kind, body)
        name = validated["name"]
        key = key_for(plural, name)
        decision = admit(kind, body, verb="create", grants=self.grants_provider(),
                         quotas=[], actor=actor)
        if not decision.allowed:
            self.audit_deny(plural, name, "create", actor,
                            decision.status, decision.reason, decision.detail)
            raise Problem(decision.status, decision.reason, decision.detail)
        stored = self.store.put(
            key,
            {
                "name": name,
                "spec": decision.spec,
                "status": validated.get("status") or {},
            },
            expected_rev=0,
        )
        return to_envelope(kind, stored)

    def update(self, plural: str, name: str, body: dict, actor: str = "") -> dict:
        kind = self._get_kind(plural)
        key = key_for(plural, name)
        old = self.store.get(key)
        if old is None:
            raise Problem(404, "NotFound", f"{kind}/{name} not found")
        rv = _resource_version(body)
        decision = admit(kind, body, verb="update", grants=self.grants_provider(),
                         quotas=[], actor=actor, prev=old)
        if not decision.allowed:
            self.audit_deny(plural, name, "update", actor,
                            decision.status, decision.reason, decision.detail)
            raise Problem(decision.status, decision.reason, decision.detail)
        stored = self.store.put(
            key,
            {
                "name": name,
                "spec": decision.spec,
                "status": old.get("status") or {},
            },
            expected_rev=rv,
        )
        return to_envelope(kind, stored)

    def replace_status(self, plural: str, name: str, body: dict,
                       actor: str = "") -> dict:
        if plural not in STATUS_SUBRESOURCE:
            raise Problem(404, "NotFound", f"{plural} has no /status")
        kind = RESOURCES[plural]
        key = key_for(plural, name)
        old = self.store.get(key)
        if old is None:
            raise Problem(404, "NotFound", f"{kind}/{name} not found")
        rv = _resource_version(body)
        new_status = body.get("status", {})
        stored = self.store.put(
            key,
            {
                "name": name,
                "spec": old.get("spec") or {},
                "status": new_status,
            },
            expected_rev=rv,
        )
        return status_envelope(kind, stored)

    def delete(self, plural: str, name: str,
               expected_rv: str | None = None, actor: str = "") -> dict:
        kind = self._get_kind(plural)
        key = key_for(plural, name)
        self.store.delete(key, expected_rev=expected_rv, actor=actor)
        return {"status": "deleted"}

    def watch(self, plural: str, from_rev: int, limit: int = 100) -> list[dict]:
        kind = self._get_kind(plural)
        results: list[dict] = []
        events = self.store.watch(from_rev, timeout=0.05)
        for evt in events:
            if len(results) >= limit:
                break
            if not evt.get("key", "").startswith(f"{plural}/"):
                continue
            event_type = evt.get("type", "")
            if event_type not in ("ADDED", "MODIFIED", "DELETED"):
                continue
            if event_type == "DELETED":
                obj = None
            else:
                val = evt.get("value")
                obj = to_envelope(kind, val) if val else None
            results.append({"type": event_type, "rev": evt.get("rev", 0),
                            "object": obj})
        return results

    def audit_deny(self, plural: str, name: str, verb: str,
                   actor: str, status: int, reason: str, detail: str) -> None:
        key = f"audits/deny-{self._deny_seq}"
        self._deny_seq += 1
        payload = {
            "auditDeny": {"verb": verb, "kind": RESOURCES.get(plural, plural),
                          "name": name, "actor": actor,
                          "status": status, "reason": reason, "detail": detail}
        }
        self.store.put(key, payload)
