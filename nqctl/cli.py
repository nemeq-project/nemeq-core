"""Minimal nqctl with ``store fsck|verify|snapshot``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from massaru import store_fsck
from massaru import instance as instance_mod
from massaru import install as install_mod
from massaru import deps as deps_mod
from massaru import unit as unit_mod

from . import commands
from .client import Client, load_config, resolve_actor, resolve_url


def build_parser(config: dict | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nqctl")
    parser.add_argument(
        "--store",
        default=os.environ.get("NEMEQ_STORE", "/var/nemeq/apiserver"),
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="apiserver URL (default: ~/.nq/config, NEMEQ_API_URL, :8448)",
    )
    parser.add_argument("--actor", default=None, help="actor for admission")
    sub = parser.add_subparsers(dest="command", required=True)
    store = sub.add_parser("store", help="store maintenance")
    actions = store.add_subparsers(dest="action", required=True)
    fsck = actions.add_parser("fsck", help="validate journal; optional repair")
    fsck.add_argument("--repair", action="store_true")
    fsck.add_argument("--remote", default=None, help="off-host watermark dir")
    verify = actions.add_parser("verify", help="strict journal validation")
    verify.add_argument("--remote", default=None, help="off-host watermark dir")
    actions.add_parser("snapshot", help="take a state snapshot")

    health = sub.add_parser("health", help="check apiserver health")
    health.set_defaults(remote_api=True, action="health")

    get = sub.add_parser("get", help="get objects via HTTP API")
    get.add_argument("kind")
    get.add_argument("name", nargs="?")
    get.set_defaults(remote_api=True, action="get")

    describe = sub.add_parser("describe", help="describe an object")
    describe.add_argument("kind")
    describe.add_argument("name")
    describe.set_defaults(remote_api=True, action="describe")

    apply_ = sub.add_parser("apply", help="apply JSON manifest")
    apply_.add_argument("-f", "--file", required=True)
    apply_.set_defaults(remote_api=True, action="apply")

    inst = sub.add_parser(
        "instance", help="instance lifecycle (create/list/status/destroy/uninstall)"
    )
    inst.add_argument("action", choices=["create", "list", "status", "destroy", "uninstall"])
    inst.add_argument("--home", default=None, help="prefix root (default: NEMEQ_HOME or ~/.nemeq)")
    inst.add_argument("--instance", default=None, help="instance name (default: NEMEQ_INSTANCE)")
    inst.add_argument("--state-root", default=None, help="override var root")
    inst.add_argument("--purge", action="store_true", help="with uninstall: remove whole prefix")
    inst.set_defaults(local_instance=True, action_name="instance")

    ins = sub.add_parser("install", help="install Nemeq into a prefix")
    ins.add_argument("--home", default=None)
    ins.add_argument("--instance", default=None)
    ins.add_argument("--state-root", default=None)
    ins.add_argument("--min-glibc", default=None, help="override required glibc")
    ins.set_defaults(local_install=True, action_name="install")

    dep = sub.add_parser("deps", help="dependency delivery (offline wheelhouse)")
    dep.add_argument("action", choices=["install", "verify", "format-check"])
    dep.add_argument("--wheelhouse", required=True, help="directory with *.whl + manifest.sha256")
    dep.add_argument("--target", required=True, help="lib/pydeps destination")
    dep.add_argument("--force", action="store_true", help="overwrite non-empty target")
    dep.set_defaults(local_deps=True, action_name="deps")

    u = sub.add_parser("install-unit", help="generate systemd unit for an instance")
    u.add_argument("--home", default=None)
    u.add_argument("--instance", default=None)
    u.add_argument("--state-root", default=None)
    u.add_argument("--store", default=None)
    u.add_argument("--system", action="store_true", help="system unit (requires root)")
    u.add_argument("--user", default=None, help="home dir for user unit")
    u.add_argument("--dry-run", action="store_true", help="print unit, do not write")
    u.set_defaults(local_unit=True, action_name="install-unit")

    watch = sub.add_parser("watch", help="watch objects")
    watch.add_argument("kind")
    watch.add_argument("--from-rev", type=int, default=0)
    watch.add_argument("--limit", type=int, default=100)
    watch.set_defaults(remote_api=True, action="watch")
    return parser


def _remote(args: argparse.Namespace) -> int:
    """Выполнить команду через HTTP API (get/describe/apply/watch/health)."""
    config = load_config()
    client = Client(resolve_url(args.api_url, config), resolve_actor(args.actor, config))
    action = args.action
    if action == "health":
        code, out = commands.cmd_health(client)
    elif action == "get":
        code, out = commands.cmd_get(client, args.kind, args.name)
    elif action == "describe":
        code, out = commands.cmd_describe(client, args.kind, args.name)
    elif action == "apply":
        try:
            manifest = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"title": "Invalid", "detail": str(exc)}))
            return 1
        code, out = commands.cmd_apply(client, manifest)
    elif action == "watch":
        code, out = commands.cmd_watch(client, args.kind, from_rev=args.from_rev, limit=args.limit)
    else:
        print(json.dumps({"title": "Invalid", "detail": "unknown action"}))
        return 2
    if out:
        print(out)
    return 0 if code < 400 else 1


def main(argv: list[str] | None = None) -> int:
    """Точка входа: HTTP-команды в _remote, локальные store-команды здесь."""
    args = build_parser().parse_args(argv)
    if getattr(args, "local_unit", False):
        code, payload = unit_mod.run_cli_unit(
            home=args.home,
            instance=args.instance,
            state_root=args.state_root,
            store=args.store,
            system=args.system,
            user=args.user,
            write=not args.dry_run,
        )
        print(json.dumps(payload, ensure_ascii=False))
        return code
    if getattr(args, "local_deps", False):
        code, payload = deps_mod.run_cli_deps(
            args.action,
            wheelhouse=args.wheelhouse,
            target=args.target,
            force=args.force,
        )
        print(json.dumps(payload, ensure_ascii=False))
        return code
    if getattr(args, "local_install", False):
        code, payload = install_mod.run_cli_install(
            home=args.home,
            instance=args.instance,
            state_root=args.state_root,
            min_glibc=args.min_glibc,
        )
        print(json.dumps(payload, ensure_ascii=False))
        return code
    if getattr(args, "local_instance", False):
        code, payload = instance_mod.run_cli_action(
            args.action,
            home=args.home,
            instance=args.instance,
            state_root=args.state_root,
            store=None,
            purge=args.purge,
        )
        print(json.dumps(payload, ensure_ascii=False))
        return code
    if getattr(args, "remote_api", False):
        return _remote(args)
    if getattr(args, "command", None) != "store":
        print(json.dumps({"title": "Invalid", "detail": "unknown action"}))
        return 2
    path = Path(args.store)
    if args.action == "snapshot":
        rev = store_fsck.snapshot(path)
        print(json.dumps({"snapshotRev": rev}))
        return 0
    if args.action == "fsck":
        report = store_fsck.fsck(path, repair=args.repair, remote=args.remote)
    else:
        report = store_fsck.verify(path, remote=args.remote)
    print(json.dumps(report.to_dict(), ensure_ascii=False))
    return 0 if report.ok else 1

if __name__ == "__main__":
    sys.exit(main())
