from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from massaru.store import Store

from apiserver.handlers import Api
from apiserver.openapi import build_openapi
from apiserver.server import serve

PUBLIC: str = "nq"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nemeq-apiserver")
    parser.add_argument("store", nargs="?", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--version", action="store_true")
    return parser


def resolve_store(args: argparse.Namespace) -> Path:
    if args.store:
        return Path(args.store)
    env = os.environ.get("NEMEQ_STORE")
    if env:
        return Path(env)
    from massaru.layout import resolve_all
    return resolve_all().store


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print("nemeq-apiserver 0.1.0")
        return 0
    store = Store(resolve_store(args))
    api = Api(store, lambda: store.list("grants/")[0])
    try:
        serve(api, host=args.host, port=args.port, openapi=build_openapi())
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
