"""CLI for the Nemeq massaru v1 skeleton.

Read-only commands:
    python -m massaru [--state-root DIR] list
    python -m massaru [--state-root DIR] inspect <epishu-id>
    python -m massaru [--state-root DIR] verify <epishu-id>

Install/bind:
    python -m massaru [--state-root DIR] install <zi> <su> [--epishu-id ID]
    python -m massaru [--state-root DIR] bind_model <epishu-id> <model>

Lifecycle commands (run/sleep/stop/commit) are explicit skeletons and exit
20 without touching the filesystem.  v1 stays read-only: no external side
effects are possible from this package.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import NOT_IMPLEMENTED_EXIT, SCHEMA, VERSION
from .images import DigestMismatch, ImageError
from .instance import (
    InstanceError,
    InstanceExists,
    InstanceMissing,
    bind_model,
    install_instance,
    verify_instance,
)
from .layout import resolve_state_root
from .store import inspect_instance, list_instances

LIFECYCLE_COMMANDS = ("run", "sleep", "stop", "commit")


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
        help="install zi+su (digest + 02b layout)",
    )
    install_parser.add_argument("zi")
    install_parser.add_argument("su")
    install_parser.add_argument("--epishu-id", default=None)

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
        help="bind a model to an installed epishu",
    )
    bind_parser.add_argument("epishu_id")
    bind_parser.add_argument("model")

    verify_parser = subparsers.add_parser(
        "verify", parents=[common], help="verify zi/su digests (read-only)"
    )
    verify_parser.add_argument("epishu_id")

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

    if args.command == "install":
        try:
            instance = install_instance(
                state_root,
                args.zi,
                args.su,
                epishu_id=args.epishu_id,
            )
        except DigestMismatch as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "command": "install",
                    "status": "error",
                    "code": "digest_mismatch",
                    "error": str(exc),
                }
            )
            return 1
        except (ImageError, InstanceExists, ValueError, OSError) as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "command": "install",
                    "status": "error",
                    "code": "install_failed",
                    "error": str(exc),
                }
            )
            return 1
        emit(
            {
                "schema": SCHEMA,
                "command": "install",
                "status": "installed",
                "epishu_id": instance["epishu_id"],
                "instance": instance,
            }
        )
        return 0

    if args.command == "bind_model":
        try:
            instance = bind_model(state_root, args.epishu_id, args.model)
        except InstanceMissing as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "command": "bind_model",
                    "status": "not_found",
                    "error": str(exc),
                }
            )
            return 1
        except DigestMismatch as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "command": "bind_model",
                    "status": "error",
                    "code": "digest_mismatch",
                    "error": str(exc),
                }
            )
            return 1
        except (ImageError, InstanceError, OSError) as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "command": "bind_model",
                    "status": "error",
                    "code": "bind_failed",
                    "error": str(exc),
                }
            )
            return 1
        emit(
            {
                "schema": SCHEMA,
                "command": "bind_model",
                "status": "bound",
                "epishu_id": instance["epishu_id"],
                "model": instance.get("model"),
            }
        )
        return 0

    if args.command == "verify":
        try:
            instance = verify_instance(state_root, args.epishu_id)
        except InstanceMissing as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "command": "verify",
                    "status": "not_found",
                    "error": str(exc),
                }
            )
            return 1
        except DigestMismatch as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "command": "verify",
                    "status": "error",
                    "code": "digest_mismatch",
                    "error": str(exc),
                }
            )
            return 1
        except (ImageError, InstanceError, OSError) as exc:
            emit(
                {
                    "schema": SCHEMA,
                    "command": "verify",
                    "status": "error",
                    "code": "verify_failed",
                    "error": str(exc),
                }
            )
            return 1
        emit(
            {
                "schema": SCHEMA,
                "command": "verify",
                "status": "ok",
                "epishu_id": instance["epishu_id"],
                "zi_digest": instance.get("zi_digest"),
                "su_digest": instance.get("su_digest"),
            }
        )
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
