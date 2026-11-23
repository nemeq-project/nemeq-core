"""CLI for the Nemeq massaru v1 skeleton.

Read-only commands:
    python -m massaru [--state-root DIR] list
    python -m massaru [--state-root DIR] inspect <epishu-id>

Lifecycle commands are explicit skeletons and exit 20 without touching the
filesystem.  This keeps the v1 read-only promise enforceable while the
massaru lifecycle lands in later PRs.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import NOT_IMPLEMENTED_EXIT, SCHEMA, VERSION
from .layout import resolve_state_root
from .store import inspect_instance, list_instances

LIFECYCLE_COMMANDS = (
    "install",
    "run",
    "sleep",
    "stop",
    "commit",
    "bind_model",
)


def emit(payload: dict[str, Any]) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--state-root",
        default=argparse.SUPPRESS,
        help="var-zone root (default: /var/nemeq; NEMEQ_VAR_ROOT for tests)",
    )

    parser = argparse.ArgumentParser(
        prog="python -m massaru",
        description="Nemeq massaru v1 skeleton (read-only)",
        parents=[common],
    )
    parser.add_argument(
        "--version", action="version", version=f"massaru {VERSION}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "list", parents=[common], help="list installed epishu"
    )

    inspect_parser = subparsers.add_parser(
        "inspect", parents=[common], help="inspect one epishu"
    )
    inspect_parser.add_argument("epishu_id")

    install_parser = subparsers.add_parser(
        "install",
        parents=[common],
        help="install zi+su (skeleton: not implemented)",
    )
    install_parser.add_argument("zi")
    install_parser.add_argument("su")

    for name in ("run", "sleep", "stop", "commit"):
        command_parser = subparsers.add_parser(
            name,
            parents=[common],
            help=f"{name} epishu (skeleton: not implemented)",
        )
        command_parser.add_argument("epishu_id")

    bind_parser = subparsers.add_parser(
        "bind_model",
        parents=[common],
        help="bind a model to an epishu (skeleton: not implemented)",
    )
    bind_parser.add_argument("epishu_id")
    bind_parser.add_argument("model")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    state_root = resolve_state_root(
        getattr(args, "state_root", None)
    )

    if args.command == "list":
        emit(
            {
                "schema": SCHEMA,
                "epishu": list_instances(state_root),
            }
        )
        return 0

    if args.command == "inspect":
        try:
            payload = inspect_instance(state_root, args.epishu_id)
        except ValueError as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "error": str(exc),
                    "epishu_id": args.epishu_id,
                }
            )
            return 2
        if payload is None:
            emit(
                {
                    "schema": SCHEMA,
                    "status": "not_found",
                    "epishu_id": args.epishu_id,
                }
            )
            return 1
        emit({"schema": SCHEMA, **payload})
        return 0

    if args.command in LIFECYCLE_COMMANDS:
        emit(
            {
                "schema": SCHEMA,
                "command": args.command,
                "status": "not_implemented",
                "read_only": True,
            }
        )
        return NOT_IMPLEMENTED_EXIT

    emit({"schema": SCHEMA, "error": "unknown command"})
    return 2


if __name__ == "__main__":
    sys.exit(main())
