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


def store_status(check_identity: bool = True) -> dict:
    """Живое состояние стора для `/healthz` (§9.3).

    `rev` и `last_hash` меняются при каждой записи, поэтому читаются НА
    КАЖДЫЙ запрос — иначе они константы и «rev не убывает» непроверяемо.

    Здесь же сверяется идентичность: `instance_id`/`store_id` из
    `instance.toml` против метаданных стора (§6.1: авторитет — стор).
    Проверка НЕ кэшируется на старте: подмена конфига при живом сторе обязана
    давать код 5, и `rev`-запросы не должны её маскировать.
    """
    status: dict = {"rev": 0, "last_hash": "", "fsck": "clean", "sandbox": {}}
    try:
        from massaru import store_fsck
        from massaru.layout import resolve_all

        resolved = resolve_all()
        if not resolved.store.is_dir():
            # `verify()` на отсутствующем сторе возвращает ok=True/rev=0:
            # «стора нет» было бы неотличимо от «стор пуст и здоров».
            status["fsck"] = "missing"
            status["errors"] = [f"store is missing: {resolved.store}"]
            return status
        report = store_fsck.verify(resolved.store)
        status.update({
            "rev": int(report.rev),
            "last_hash": str(report.last_hash),
            "fsck": "clean" if report.ok else "dirty",
            "errors": list(report.errors),
        })

        if check_identity:
            from massaru.instance import check_store_identity, load

            info = load(resolved)
            check = check_store_identity(resolved, info)
            if check["mismatch"]:
                status["identityMismatch"] = True
                status["detail"] = check["detail"]
    except Exception as exc:  # noqa: BLE001 - health не должен падать
        status["fsck"] = "unavailable"
        status["errors"] = [str(exc)]
    return status


def identity_snapshot() -> tuple[dict, dict]:
    """Снимок идентичности и состояния стора для `/healthz` (§9.3).

    Значения читаются из РЕАЛЬНОГО стора через `store_fsck.verify()`:
    `rev`, `last_hash` и `fsck` — это его фактические данные, а не константы.
    Иначе требование «rev не убывает, last_hash совпадает» непроверяемо:
    константы совпадают всегда.

    `instance_id` сверяется с метаданными стора (§6.1: авторитет — стор).
    Расхождение выставляет `identityMismatch`, и `/healthz` отдаёт код 5 —
    «сервер лежит» и «identity разъехалась» обязаны различаться.

    Отсутствие `instance.toml` — штатный режим «инстанса нет»: возвращается
    пустая identity, и health отвечает `status: no-instance`, а не `ok`.
    """
    identity: dict = {}
    status: dict = {"rev": 0, "last_hash": "", "fsck": "clean", "sandbox": {}}
    try:
        from massaru.instance import check_store_identity, load
        from massaru.layout import resolve_all

        resolved = resolve_all()
        info = load(resolved)
        identity = {
            "instance": info.name,
            "home_id": info.home_id,
            "store_id": info.store_id,
            "instance_id": info.instance_id,
        }

        status.update(store_status())

        check = check_store_identity(resolved, info)
        if check["mismatch"]:
            status["identityMismatch"] = True
            status["detail"] = check["detail"]
    except Exception as exc:  # noqa: BLE001 - сервер поднимается и без инстанса
        # Диагностика не должна маскировать причину: пишем в stderr, а не молча.
        print(f"health: identity unavailable: {exc}", file=sys.stderr, flush=True)
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
              identity=identity, store_status=status,
              store_status_fn=store_status)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
