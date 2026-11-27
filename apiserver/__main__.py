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


def identity_snapshot() -> tuple[dict, dict]:
    """Снимок идентичности и состояния стора для `/healthz` (§9.3).

    Читается ОДИН раз при старте: `/health` — инвариант живого процесса, а не
    перечитывание файлов на каждый запрос (§6.1). Отсутствие `instance.toml`
    — штатный режим «сервер без инстанса»: тогда health отдаёт минимум,
    а не падает на старте.
    """
    identity: dict = {}
    status: dict = {"rev": 0, "last_hash": "", "fsck": "clean", "sandbox": {}}
    try:
        from massaru.instance import load
        from massaru.layout import resolve_all

        resolved = resolve_all()
        info = load(resolved)
        identity = {
            "instance": info.name,
            "home_id": info.home_id,
            "store_id": info.store_id,
            "instance_id": info.instance_id,
        }
    except Exception:  # noqa: BLE001 - сервер обязан подняться и без инстанса
        pass
    return identity, status


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print("nemeq-apiserver 0.1.0")
        return 0
    store_path = resolve_store(args)
    store = Store(store_path)
    api = Api(store, lambda: store.list("grants/")[0])
    identity, status = identity_snapshot()
    try:
        serve(api, host=args.host, port=args.port, openapi=build_openapi(),
              identity=identity, store_status=status)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
