"""Offline fsck/verify/snapshot helpers for TEXTSTORE v0.1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import FsckReport, Store, scan_store


def fsck(path: str | Path, repair: bool = False) -> FsckReport:
    """Validate the journal; optionally trim a torn tail in the last segment."""
    return scan_store(path, repair=repair).report


def verify(path: str | Path) -> FsckReport:
    """Strict validation: no repair, report every error."""
    return scan_store(path, repair=False).report


def snapshot(path: str | Path) -> int:
    """Take a materialized snapshot through the writer."""
    with Store(path) as store:
        return store.snapshot()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="store_fsck")
    parser.add_argument("action", choices=("fsck", "verify", "snapshot"))
    parser.add_argument("--store", default="/var/lib/nemeq/apiserver")
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "snapshot":
        print(json.dumps({"snapshotRev": snapshot(args.store)}))
        return 0
    report = fsck(args.store, repair=args.repair) if args.action == "fsck" else verify(args.store)
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
