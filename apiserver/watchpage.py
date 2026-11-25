"""Watch page builder working on the GLOBAL ordered event stream.

The helper walks every event it looks at - including other resources and
events filtered out by scope - so ``metadata.nextRev`` is a global progress
watermark.  ``items`` only contains this resource's visible objects.
"""

from __future__ import annotations

from typing import Any

from .authz import visible
from .envelope import to_envelope


def event_object(kind: str, event: dict) -> dict | None:
    """Outward object: DELETED is always null (the previous value stays internal)."""
    if event.get("type") == "DELETED":
        return None
    value = event.get("value")
    return to_envelope(kind, value) if value else None


def event_scope(event: dict) -> tuple[str, dict[str, Any]]:
    """Name/spec used for scope authz; DELETED uses the internal previous value."""
    key = event.get("key", "")
    fallback = key.rsplit("/", 1)[-1] if "/" in key else key
    value = event.get("value")
    if isinstance(value, dict):
        name = value.get("name") or fallback
        spec = value.get("spec")
        return str(name), (spec if isinstance(spec, dict) else {})
    return fallback, {}


def page_events(
    kind: str,
    plural: str,
    events: list[dict],
    *,
    from_rev: int,
    limit: int,
    grants: list[dict],
    actor: str,
) -> dict:
    """Return one watch page from the global stream.

    Invariants:
    * ``nextRev`` advances on every event actually inspected, including other
      resources and scope-filtered ones.
    * ``items`` only holds this resource (plural prefix) and visible scopes.
    * A rev group (same ``rev``, several ``seq``) is never split by ``limit``;
      an oversized group is returned whole.
    """
    prefix = plural + "/"
    items: list[dict] = []
    next_rev = int(from_rev)
    index = 0
    total = len(events)
    while index < total:
        rev = int(events[index].get("rev", 0))
        if rev <= from_rev:
            index += 1
            continue
        group_end = index
        while group_end < total and int(events[group_end].get("rev", 0)) == rev:
            group_end += 1
        group = [
            event for event in events[index:group_end]
            if str(event.get("key", "")).startswith(prefix)
        ]
        eligible: list[dict] = []
        for event in group:
            name, spec = event_scope(event)
            if visible(grants, "watch", kind, name, spec, actor):
                eligible.append(
                    {
                        "type": event.get("type"),
                        "rev": rev,
                        "object": event_object(kind, event),
                    }
                )
        if items and len(items) + len(eligible) > limit:
            break
        next_rev = rev
        index = group_end
        items.extend(eligible)
        if len(items) >= limit:
            break
    return {
        "items": items,
        "metadata": {"nextRev": next_rev, "fromRev": int(from_rev), "limit": limit},
    }


def next_rev_of(page: dict) -> int:
    return int(page["metadata"]["nextRev"])
