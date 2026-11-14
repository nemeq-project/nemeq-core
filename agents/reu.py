#!/usr/bin/env python3
"""reu - Nemeq watchdog.

CLI:
    reu.py run [--manifest PATH] [--run-dir DIR]
    reu.py status [--manifest PATH] [--run-dir DIR]

reu never watches init or itself.  It discovers the PIDs started by init from
``init_status.json``, watches the manifest entries with ``watch=true``, and
spawns a replacement when a watched PID disappears.  ``init status`` merges
these replacement PIDs from ``reu_status.json`` so the process tree remains
observable.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

INIT_STATUS_NAME = "init_status.json"
REU_STATUS_NAME = "reu_status.json"


def _manifest_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    configured = os.environ.get("NEMEQ_MANIFEST")
    if configured:
        return Path(configured).resolve()
    return Path("riksu.toml").resolve()


def resolve_run_dir(manifest_path: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    configured = os.environ.get("NEMEQ_RUN_DIR")
    if configured:
        return Path(configured).resolve()
    try:
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
        relative = manifest.get("runtime", {}).get("run_dir", "run")
    except (OSError, tomllib.TOMLDecodeError):
        relative = "run"
    return (manifest_path.parent / str(relative)).resolve()


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    with manifest_path.open("rb") as handle:
        manifest = tomllib.load(handle)
    if not isinstance(manifest.get("agents"), dict):
        raise ValueError("manifest has no [agents] table")
    return manifest


def watched_specs(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    watched: dict[str, dict[str, Any]] = {}
    for name, spec in manifest.get("agents", {}).items():
        if name in {"init", "reu"} or not isinstance(spec, dict):
            continue
        if spec.get("watch", True):
            watched[name] = spec
    return watched


def agent_command(spec: dict[str, Any], manifest_dir: Path) -> list[str]:
    module = Path(str(spec.get("module", "")))
    if not module.is_absolute():
        module = manifest_dir / module
    args = spec.get("args", ["run"])
    if not isinstance(args, list):
        raise ValueError("agents.*.args must be a list")
    return [sys.executable, str(module), *[str(item) for item in args]]


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


def build_status(
    records: dict[str, dict[str, Any]], pid: int
) -> dict[str, Any]:
    return {
        "pid": pid,
        "updated": time.time(),
        "agents": {
            name: {
                "pid": record["pid"],
                "alive": bool(record["alive"]),
                "restarts": int(record["restarts"]),
                "last_reason": record["last_reason"],
                "exit_code": record.get("exit_code"),
            }
            for name, record in records.items()
        },
    }


def _spawn_replacement(
    spec: dict[str, Any], manifest_path: Path, env: dict[str, str]
) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        agent_command(spec, manifest_path.parent),
        cwd=str(manifest_path.parent),
        env=env,
        start_new_session=True,
    )


def collect_init_agents(init_status: Any) -> dict[str, dict[str, Any]]:
    agents: dict[str, dict[str, Any]] = {}
    if not isinstance(init_status, dict):
        return agents
    for entry in init_status.get("agents", []):
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            agents[entry["name"]] = entry
    return agents


def adopt_missing(
    records: dict[str, dict[str, Any]],
    init_agents: dict[str, dict[str, Any]],
    alive_fn=pid_alive,
) -> bool:
    changed = False
    for name, record in records.items():
        if record["pid"] is not None:
            continue
        entry = init_agents.get(name)
        if entry and entry.get("pid"):
            record["pid"] = int(entry["pid"])
            record["alive"] = alive_fn(record["pid"])
            changed = True
    return changed


def refresh_exit_codes(
    records: dict[str, dict[str, Any]],
    init_agents: dict[str, dict[str, Any]],
    children: dict[str, subprocess.Popen[Any]],
) -> None:
    for name, record in records.items():
        entry = init_agents.get(name)
        if (
            entry
            and entry.get("pid") == record["pid"]
            and entry.get("state") == "dead"
            and entry.get("exit_code") is not None
        ):
            record["exit_code"] = int(entry["exit_code"])
        child = children.get(name)
        if child is not None:
            return_code = child.poll()
            if return_code is not None:
                record["exit_code"] = return_code


def restart_dead(
    records: dict[str, dict[str, Any]],
    watched: dict[str, dict[str, Any]],
    spawn_fn,
    alive_fn=pid_alive,
    stopping: bool = False,
) -> bool:
    changed = False
    for name, record in records.items():
        if record["pid"] is None:
            continue
        if alive_fn(record["pid"]):
            if not record["alive"]:
                record["alive"] = True
                changed = True
            record["exit_code"] = None
            record["gone_since"] = None
            continue

        if record["alive"]:
            record["alive"] = False
            changed = True
        if record.get("gone_since") is None:
            record["gone_since"] = time.monotonic()
            changed = True
        if stopping:
            continue

        exit_code = record.get("exit_code")
        if exit_code == 0:
            if record["last_reason"] != "clean_exit":
                record["last_reason"] = "clean_exit"
                changed = True
            continue
        if (
            exit_code is None
            and time.monotonic() - record["gone_since"] < 1.0
        ):
            # Give init a chance to reap the child and publish its exit code;
            # this is what distinguishes a clean exit from a crash.
            continue

        record["restarts"] = int(record["restarts"]) + 1
        record["last_reason"] = "process_gone"
        changed = True
        try:
            replacement = spawn_fn(name)
        except (OSError, ValueError, TypeError) as exc:
            record["last_reason"] = f"spawn_failed:{exc}"
            print(
                f"[reu] restart {name} failed: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue
        record["pid"] = replacement.pid
        record["alive"] = True
        record["exit_code"] = None
        record["gone_since"] = None
        print(
            f"[reu] restarted {name} pid={replacement.pid} "
            f"restarts={record['restarts']}",
            file=sys.stderr,
            flush=True,
        )
    return changed


def run_service(manifest_path: Path, run_dir: Path) -> int:
    manifest = load_manifest(manifest_path)
    watched = watched_specs(manifest)
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / REU_STATUS_NAME
    init_status_path = run_dir / INIT_STATUS_NAME

    env = os.environ.copy()
    env["NEMEQ_MANIFEST"] = str(manifest_path)
    env["NEMEQ_RUN_DIR"] = str(run_dir)
    env["PYTHONUNBUFFERED"] = "1"

    records: dict[str, dict[str, Any]] = {
        name: {
            "pid": None,
            "alive": False,
            "restarts": 0,
            "last_reason": None,
            "exit_code": None,
            "gone_since": None,
        }
        for name in watched
    }
    children: dict[str, subprocess.Popen[Any]] = {}

    stop = threading.Event()

    def handle_signal(signum: int, frame: Any) -> None:
        stop.set()

    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, handle_signal)

    _write_json_atomic(status_path, build_status(records, os.getpid()))
    print(
        f"[reu] running pid={os.getpid()} watching={sorted(watched)}",
        file=sys.stderr,
        flush=True,
    )
    def spawn(name: str) -> subprocess.Popen[Any]:
        replacement = _spawn_replacement(
            watched[name], manifest_path, env
        )
        children[name] = replacement
        return replacement

    try:
        while not stop.is_set():
            for child in children.values():
                child.poll()

            init_agents = collect_init_agents(
                _read_json(init_status_path, {})
            )
            changed = adopt_missing(records, init_agents)
            refresh_exit_codes(records, init_agents, children)
            changed = (
                restart_dead(
                    records,
                    watched,
                    spawn,
                    stopping=stop.is_set(),
                )
                or changed
            )

            if changed:
                _write_json_atomic(
                    status_path, build_status(records, os.getpid())
                )
            stop.wait(0.1)
    finally:
        for child in children.values():
            if child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5.0
        for child in children.values():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for record in records.values():
            record["alive"] = False
        _write_json_atomic(status_path, build_status(records, os.getpid()))
    return 0


def status_command(run_dir: Path) -> int:
    payload = _read_json(run_dir / REU_STATUS_NAME)
    if not isinstance(payload, dict):
        print("[reu] status not found", file=sys.stderr, flush=True)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reu")
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)

    manifest_path = _manifest_path(args.manifest)
    run_dir = resolve_run_dir(manifest_path, args.run_dir)

    if args.command == "status":
        return status_command(run_dir)
    try:
        return run_service(manifest_path, run_dir)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"[reu] {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
