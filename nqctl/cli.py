"""Minimal nqctl with ``store fsck|verify|snapshot``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from massaru import store_fsck


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nqctl")
    parser.add_argument(
        "--store",
        default=os.environ.get("NEMEQ_STORE", "/var/nemeq/apiserver"),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    store = sub.add_parser("store", help="store maintenance")
    actions = store.add_subparsers(dest="action", required=True)
    fsck = actions.add_parser("fsck", help="validate journal; optional repair")
    fsck.add_argument("--repair", action="store_true")
    actions.add_parser("verify", help="strict journal validation")
    actions.add_parser("snapshot", help="take a state snapshot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.store)
    if args.action == "snapshot":
        rev = store_fsck.snapshot(path)
        print(json.dumps({"snapshotRev": rev}))
        return 0
    report = (
        store_fsck.fsck(path, repair=args.repair)
        if args.action == "fsck"
        else store_fsck.verify(path)
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
