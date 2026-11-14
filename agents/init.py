#!/usr/bin/env python3
"""init - Nemeq PID-1 agent for the PoC.

CLI:
    init.py run [--manifest PATH] [--run-dir DIR]
    init.py status [--manifest PATH] [--run-dir DIR]

init reads ``[agents]`` from riksu.toml in declaration order, spawns each agent
with ``subprocess.Popen`` (never through a shell), writes live PIDs to
``init_status.json``, forwards SIGTERM/SIGINT, and waits for its children.
reu is the watchdog; init status merges reu's replacement PIDs so the process
tree stays observable after a restart.
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

STATUS_NAME = "init_status.json"
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
    agents = manifest.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise ValueError("manifest has no non-empty [agents] table")
    for name, spec in agents.items():
        if not isinstance(spec, dict) or "module" not in spec:
            raise ValueError(f"agents.{name}.module is required")
    return manifest


def agent_command(spec: dict[str, Any], manifest_dir: Path) -> list[str]:
    module = Path(str(spec["module"]))
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
    records: dict[str, dict[str, Any]], init_pid: int, running: bool
) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    for name, record in records.items():
        process = record["proc"]
        return_code = process.poll()
        state = (
            "running"
            if return_code is None and record.get("state") != "dead"
            else "dead"
        )
        agents.append(
            {
                "name": name,
                "pid": process.pid,
                "state": state,
                "exit_code": return_code,
            }
        )
    return {
        "init_pid": init_pid,
        "running": running,
        "updated": time.time(),
        "agents": agents,
    }


def merge_with_reu(
    status: dict[str, Any], reu_status: Any
) -> dict[str, Any]:
    if not isinstance(reu_status, dict):
        return status
    replacements = reu_status.get("agents", {})
    if not isinstance(replacements, dict):
        return status
    for agent in status.get("agents", []):
        if not isinstance(agent, dict):
            continue
        entry = replacements.get(agent.get("name"))
        if (
            isinstance(entry, dict)
            and entry.get("alive")
            and entry.get("pid")
        ):
            agent["pid"] = int(entry["pid"])
            agent["state"] = "running"
            agent["exit_code"] = None
            agent["restarts"] = int(entry.get("restarts", 0))
    return status


def status_command(run_dir: Path) -> int:
    status = _read_json(run_dir / STATUS_NAME)
    if not isinstance(status, dict):
        print(
            json.dumps(
                {
                    "agents": [],
                    "running": False,
                    "error": "not_running",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    status = merge_with_reu(
        status, _read_json(run_dir / REU_STATUS_NAME, {})
    )
    init_pid = status.get("init_pid")
    alive = pid_alive(init_pid) if init_pid else False
    status["running"] = bool(status.get("running") and alive)
    print(json.dumps(status, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if alive else 1


def run_service(manifest_path: Path, run_dir: Path) -> int:
    manifest = load_manifest(manifest_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / STATUS_NAME

    env = os.environ.copy()
    env["NEMEQ_MANIFEST"] = str(manifest_path)
    env["NEMEQ_RUN_DIR"] = str(run_dir)
    env["PYTHONUNBUFFERED"] = "1"

    stop = threading.Event()

    def handle_signal(signum: int, frame: Any) -> None:
        stop.set()

    previous_handlers = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in (signal.SIGTERM, signal.SIGINT)
    }
    for signal_number in previous_handlers:
        signal.signal(signal_number, handle_signal)

    records: dict[str, dict[str, Any]] = {}
    print(
        f"[init] starting manifest={manifest_path} run_dir={run_dir}",
        file=sys.stderr,
        flush=True,
    )
    try:
        for name, spec in manifest["agents"].items():
            command = agent_command(spec, manifest_path.parent)
            process = subprocess.Popen(
                command,
                cwd=str(manifest_path.parent),
                env=env,
                start_new_session=True,
            )
            records[name] = {
                "proc": process,
                "spec": spec,
                "state": "running",
            }
            _write_json_atomic(
                status_path,
                build_status(records, os.getpid(), True),
            )
            print(
                f"[init] started {name} pid={process.pid}",
                file=sys.stderr,
                flush=True,
            )

        while not stop.is_set():
            changed = False
            for record in records.values():
                return_code = record["proc"].poll()
                if return_code is not None and record["state"] == "running":
                    record["state"] = "dead"
                    record["exit_code"] = return_code
                    changed = True
                    print(
                        f"[init] child pid={record['proc'].pid} "
                        f"exited rc={return_code}",
                        file=sys.stderr,
                        flush=True,
                    )
            if changed:
                _write_json_atomic(
                    status_path,
                    build_status(records, os.getpid(), True),
                )
            if all(
                record["proc"].poll() is not None
                for record in records.values()
            ):
                break
            stop.wait(0.1)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"[init] {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        for record in records.values():
            process = record["proc"]
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5.0
        for record in records.values():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                record["proc"].wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                record["proc"].kill()
                record["proc"].wait()
        _write_json_atomic(
            status_path,
            build_status(records, os.getpid(), False),
        )
        for signal_number, previous in previous_handlers.items():
            signal.signal(signal_number, previous)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="init")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "status"))
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
        print(f"[init] {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
