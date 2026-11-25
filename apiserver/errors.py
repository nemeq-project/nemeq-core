from __future__ import annotations

import json
from typing import Any

CONTENT_TYPE: str = "application/problem+json"

REASONS: dict[int, str] = {
    400: "BadRequest",
    403: "Forbidden",
    404: "NotFound",
    405: "MethodNotAllowed",
    409: "Conflict",
    410: "Expired",
    413: "TooLarge",
    415: "UnsupportedMediaType",
    422: "Invalid",
    429: "TooManyRequests",
    500: "Internal",
    503: "Unavailable",
    507: "InsufficientStorage",
}

_STORE_MAP: dict[str, int] = {
    "ConflictError": 409,
    "StoreBusy": 409,
    "WatchGone": 410,
    "PayloadTooLarge": 413,
    "InvalidRequest": 400,
    "StoreReadOnly": 503,
    "StoreCorrupt": 500,
    "DurabilityError": 500,
}

_DETAIL_EXC: tuple[str, ...] = ("WatchGone", "ConflictError")


class Problem(Exception):
    def __init__(
        self,
        status: int,
        reason: str,
        detail: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status: int = status
        self.reason: str = reason
        self.detail: str = detail
        self.headers: dict[str, str] | None = headers
        self.type_uri: str = f"https://nemeq.io/problems/{reason}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_uri,
            "title": self.reason,
            "status": self.status,
            "detail": self.detail,
            "reason": self.reason,
        }

    def __str__(self) -> str:
        return json.dumps(self.to_dict())


def problem(
    status: int,
    detail: str = "",
    reason: str | None = None,
    headers: dict[str, str] | None = None,
) -> Problem:
    if reason is None:
        reason = REASONS.get(status, "Error")
    return Problem(status=status, reason=reason, detail=detail, headers=headers)


def from_store_error(exc: BaseException) -> Problem:
    cls_name = type(exc).__name__
    # The explicit map is the single source of truth for the HTTP status of
    # known store errors; anything else is a 500. Do not trust exc.code here:
    # that would make the contract untestable and silently override the map.
    mapped_status = _STORE_MAP.get(cls_name)
    if mapped_status is None:
        code = getattr(exc, "code", None)
        mapped_status = code if isinstance(code, int) and code in REASONS else 500

    if cls_name in _DETAIL_EXC:
        detail = str(exc)
    else:
        detail = f"{cls_name}: {exc}"

    reason = REASONS.get(mapped_status, "Internal")
    return Problem(status=mapped_status, reason=reason, detail=detail)
