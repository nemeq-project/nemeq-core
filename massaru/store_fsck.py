"""Offline fsck/verify/snapshot helpers for TEXTSTORE."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import FsckReport, Store, scan_store


def fsck(
    path: str | Path,
    repair: bool = False,
    remote: str | Path | None = None,
) -> FsckReport:
    """Validate the journal; optionally trim a torn/uncommitted tail."""
    return scan_store(path, repair=repair, remote=remote).report


def verify(
    path: str | Path, remote: str | Path | None = None
) -> FsckReport:
    """Strict validation: no repair, report every error."""
    return scan_store(path, repair=False, remote=remote).report


def snapshot(path: str | Path) -> int:
    """Take a materialized snapshot through the writer."""
    with Store(path) as store:
        return store.snapshot()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="store_fsck")
    parser.add_argument("action", choices=("fsck", "verify", "snapshot"))
    parser.add_argument("--store", default="/var/nemeq/apiserver")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--remote", default=None)
    args = parser.parse_args(argv)
    if args.action == "snapshot":
        print(json.dumps({"snapshotRev": snapshot(args.store)}))
        return 0
    report = (
        fsck(args.store, repair=args.repair, remote=args.remote)
        if args.action == "fsck"
        else verify(args.store, remote=args.remote)
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
