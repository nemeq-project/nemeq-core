from __future__ import annotations

from .authz import visible
from .errors import Problem

PAGE_DEFAULT: int = 100
PAGE_MAX: int = 1000


def normalize_page(limit: int | None, continue_token: str | None) -> tuple[int, int]:
    if limit is None:
        limit = PAGE_DEFAULT
    if limit < 1 or limit > PAGE_MAX:
        raise Problem(400, "BadRequest", "limit must be 1..1000")
    offset = 0
    if continue_token:
        if not continue_token.isdigit() or int(continue_token) > 10**9:
            raise Problem(400, "BadRequest", "invalid continue token")
        offset = int(continue_token)
    return (limit, offset)


def filter_items(grants: list[dict], kind: str, items: list[dict], actor: str) -> list[dict]:
    return [item for item in items
            if visible(grants, "list", kind, item["name"], item.get("spec") or {}, actor)]


def paginate(items: list[dict], limit: int, offset: int) -> tuple[list[dict], str | None]:
    page = items[offset:offset + limit]
    next_offset = offset + len(page)
    if next_offset < len(items):
        return (page, str(next_offset))
    return (page, None)


def page_list(grants: list[dict], kind: str, items: list[dict], actor: str,
              limit: int | None, continue_token: str | None) -> tuple[list[dict], str | None]:
    """Offset counts inside the already-filtered list (no global leak)."""
    limit, offset = normalize_page(limit, continue_token)
    visible_items = filter_items(grants, kind, items, actor)
    return paginate(visible_items, limit, offset)
