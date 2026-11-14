#!/usr/bin/env python3
"""capd - Nemeq capability service (qatum registry).

CLI:
    capd.py run [--run-dir DIR]
    capd.py grant <agent> <capability> [--run-dir DIR]
    capd.py revoke <agent> <capability> [--run-dir DIR]
    capd.py check <agent> <capability> [--run-dir DIR]
    capd.py status [--run-dir DIR]

The registry is a JSON file in the run zone.  Mutations are serialised with
``fcntl.flock`` and persisted with tmp-file + ``os.replace`` so a concurrent
reader never sees a half-written state.  ``run`` is the long-lived service
started by init; grants and checks use the same file.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator

STATE_NAME = "capd_state.json"
STATUS_NAME = "capd_status.json"
LOCK_NAME = "capd.lock"


def _default_run_dir() -> Path:
    configured = os.environ.get("NEMEQ_RUN_DIR")
    if configured:
        return Path(configured).resolve()
    return Path("run").resolve()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def load_state(path: Path) -> dict[str, Any]:
    payload = _read_json(path, {"grants": {}})
    if not isinstance(payload, dict):
        payload = {"grants": {}}
    grants = payload.get("grants", {})
    if not isinstance(grants, dict):
        grants = {}
    clean: dict[str, list[str]] = {}
    for agent, capabilities in grants.items():
        if not isinstance(agent, str) or not isinstance(capabilities, list):
            continue
        clean[agent] = sorted({str(capability) for capability in capabilities})
    return {"grants": clean}


def save_state_atomic(path: Path, state: dict[str, Any]) -> None:
    _write_json_atomic(path, state)


def has_grant(state: dict[str, Any], agent: str, capability: str) -> bool:
    return capability in state.get("grants", {}).get(agent, [])


def grant(state: dict[str, Any], agent: str, capability: str) -> bool:
    capabilities = state.setdefault("grants", {}).setdefault(agent, [])
    if capability in capabilities:
        return False
    capabilities.append(capability)
    capabilities.sort()
    return True


def revoke(state: dict[str, Any], agent: str, capability: str) -> bool:
    capabilities = state.setdefault("grants", {}).get(agent)
    if not capabilities or capability not in capabilities:
        return False
    capabilities.remove(capability)
    if not capabilities:
        state["grants"].pop(agent, None)
    return True


@contextlib.contextmanager
def locked_state(run_dir: Path) -> Iterator[dict[str, Any]]:
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / LOCK_NAME
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield load_state(run_dir / STATE_NAME)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _write_status(run_dir: Path, state: str) -> None:
    _write_json_atomic(
        run_dir / STATUS_NAME,
        {
            "pid": os.getpid(),
            "state": state,
            "updated": time.time(),
        },
    )


def run_service(run_dir: Path) -> int:
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / STATE_NAME
    if not state_path.exists():
        save_state_atomic(state_path, {"grants": {}})

    stop = threading.Event()

    def handle_signal(signum: int, frame: Any) -> None:
        stop.set()

    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, handle_signal)

    _write_status(run_dir, "running")
    print(f"[capd] running pid={os.getpid()}", file=sys.stderr, flush=True)
    try:
        while not stop.wait(0.2):
            pass
    finally:
        _write_status(run_dir, "stopped")
    return 0


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capd")
    parser.add_argument(
        "command",
        choices=("run", "grant", "revoke", "check", "status"),
    )
    parser.add_argument("agent", nargs="?")
    parser.add_argument("capability", nargs="?")
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)

    run_dir = (
        Path(args.run_dir).resolve()
        if args.run_dir
        else _default_run_dir()
    )

    if args.command == "run":
        return run_service(run_dir)

    if args.command == "status":
        payload = _read_json(run_dir / STATUS_NAME)
        if not isinstance(payload, dict):
            print("[capd] status not found", file=sys.stderr, flush=True)
            return 1
        _print_json(payload)
        return 0

    if not args.agent or not args.capability:
        print(
            "[capd] grant/revoke/check require <agent> <capability>",
            file=sys.stderr,
            flush=True,
        )
        return 2

    with locked_state(run_dir) as state:
        if args.command == "grant":
            changed = grant(state, args.agent, args.capability)
            save_state_atomic(run_dir / STATE_NAME, state)
            _print_json(
                {
                    "agent": args.agent,
                    "capability": args.capability,
                    "granted": True,
                    "changed": changed,
                }
            )
            return 0
        if args.command == "revoke":
            changed = revoke(state, args.agent, args.capability)
            save_state_atomic(run_dir / STATE_NAME, state)
            _print_json(
                {
                    "agent": args.agent,
                    "capability": args.capability,
                    "granted": False,
                    "changed": changed,
                }
            )
            return 0

        granted = has_grant(state, args.agent, args.capability)
        _print_json(
            {
                "agent": args.agent,
                "capability": args.capability,
                "granted": granted,
            }
        )
        return 0 if granted else 1


if __name__ == "__main__":
    sys.exit(main())
