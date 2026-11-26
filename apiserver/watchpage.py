from __future__ import annotations

from typing import Any

from .authz import visible
from .envelope import to_envelope


def event_object(kind: str, event: dict) -> dict | None:
    """Конвертирует событие в envelope. DELETED → None, value=None → None."""
    if event.get("type") == "DELETED":
        return None
    value = event.get("value")
    if value is None:
        return None
    return to_envelope(kind, value)


def event_scope(event: dict) -> tuple[str, dict[str, Any]]:
    """Извлекает name/spec из события. DELETED value — предыдущий объект для scope."""
    key = event.get("key", "")
    fallback = key.rsplit("/", 1)[-1] if "/" in key else key
    value = event.get("value")
    if isinstance(value, dict):
        name = value.get("name") or fallback
        spec = value.get("spec") if isinstance(value.get("spec"), dict) else {}
        return str(name), spec
    return fallback, {}


def page_events(kind: str, plural: str, events: list[dict], *, from_rev: int, limit: int,
                grants: list[dict], actor: str) -> dict:
    """
    Инварианты:
      * nextRev двигается по КАЖДОМУ просмотренному событию, включая чужие plural и отфильтрованные по scope.
      * items содержит только свой plural и только видимые scope.
      * Одна rev-группа не разрезается лимитом: просмотренные события считаются отданными целиком.
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
        group = [e for e in events[index:group_end] if str(e.get("key", "")).startswith(prefix)]
        eligible: list[dict] = []
        for e in group:
            name, spec = event_scope(e)
            if visible(grants, "watch", kind, name, spec, actor):
                eligible.append({"type": e.get("type"), "rev": rev,
                                 "object": event_object(kind, e)})
        if items and len(items) + len(eligible) > limit:
            break
        next_rev = rev
        index = group_end
        items.extend(eligible)
        if len(items) >= limit:
            break
    return {"items": items,
            "metadata": {"nextRev": next_rev, "fromRev": int(from_rev), "limit": limit}}


def next_rev_of(page: dict) -> int:
    """Возвратить глобальную ревизию последнего просмотренного события в странице."""
    return int(page["metadata"]["nextRev"])
