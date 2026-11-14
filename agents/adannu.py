#!/usr/bin/env python3
"""adannu - Nemeq scheduler and sleep trigger.

CLI:
    adannu.py run [--manifest PATH] [--run-dir DIR]
    adannu.py status [--manifest PATH] [--run-dir DIR]
    adannu.py notify-overflow [--manifest PATH] [--run-dir DIR]

The run loop appends one ``sleep_requested`` JSON line per schedule tick to
``adannu_events.jsonl``.  A context-overflow marker is consumed separately and
never produces a sleep event: that is the observable property from the brief.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

STATUS_NAME = "adannu_status.json"
EVENTS_NAME = "adannu_events.jsonl"
OVERFLOW_NAME = "adannu_overflow.json"


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


def load_config(manifest_path: Path) -> dict[str, Any]:
    with manifest_path.open("rb") as handle:
        manifest = tomllib.load(handle)
    schedule = manifest.get("schedule", {})
    if not isinstance(schedule, dict):
        schedule = {}
    interval = float(schedule.get("interval", 1.0))
    if interval <= 0:
        raise ValueError("schedule.interval must be positive")
    event = str(schedule.get("event", "sleep_requested"))
    return {"interval": interval, "event": event}


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


def write_status(run_dir: Path, state: dict[str, Any]) -> None:
    _write_json_atomic(run_dir / STATUS_NAME, state)


def read_status(run_dir: Path) -> dict[str, Any] | None:
    payload = _read_json(run_dir / STATUS_NAME)
    return payload if isinstance(payload, dict) else None


def append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def notify_overflow(run_dir: Path) -> int:
    _write_json_atomic(
        run_dir / OVERFLOW_NAME,
        {"event": "context_overflow", "timestamp": time.time()},
    )
    print(
        json.dumps(
            {"event": "context_overflow", "queued": True},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def consume_overflow(run_dir: Path, state: dict[str, Any]) -> bool:
    marker = run_dir / OVERFLOW_NAME
    payload = _read_json(marker)
    if not isinstance(payload, dict):
        return False
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    state["overflow_events"] = int(state.get("overflow_events", 0)) + 1
    state["updated"] = time.time()
    print(
        "[adannu] context_overflow ignored (no sleep_requested)",
        file=sys.stderr,
        flush=True,
    )
    return True


def run_service(manifest_path: Path, run_dir: Path) -> int:
    config = load_config(manifest_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    interval = config["interval"]
    state: dict[str, Any] = {
        "pid": os.getpid(),
        "event": config["event"],
        "interval": interval,
        "fired": 0,
        "overflow_events": 0,
        "next_fire": time.time() + interval,
        "updated": time.time(),
    }
    write_status(run_dir, state)

    stop = threading.Event()

    def handle_signal(signum: int, frame: Any) -> None:
        stop.set()

    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, handle_signal)

    next_monotonic = time.monotonic() + interval
    print(
        f"[adannu] running pid={os.getpid()} interval={interval}",
        file=sys.stderr,
        flush=True,
    )
    try:
        while not stop.is_set():
            if consume_overflow(run_dir, state):
                write_status(run_dir, state)

            now = time.monotonic()
            if now >= next_monotonic:
                event_payload = {
                    "event": config["event"],
                    "timestamp": time.time(),
                    "source": "schedule",
                }
                append_event(run_dir / EVENTS_NAME, event_payload)
                print(
                    json.dumps(
                        event_payload, ensure_ascii=False, sort_keys=True
                    ),
                    flush=True,
                )
                state["fired"] = int(state["fired"]) + 1
                next_monotonic = now + interval
                state["next_fire"] = time.time() + (
                    next_monotonic - now
                )
                state["updated"] = time.time()
                write_status(run_dir, state)

            remaining = max(0.0, next_monotonic - time.monotonic())
            stop.wait(min(0.1, remaining))
    finally:
        state["updated"] = time.time()
        write_status(run_dir, state)
    return 0


def status_command(run_dir: Path) -> int:
    payload = read_status(run_dir)
    if payload is None:
        print("[adannu] status not found", file=sys.stderr, flush=True)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adannu")
    parser.add_argument(
        "command", choices=("run", "status", "notify-overflow")
    )
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)

    manifest_path = _manifest_path(args.manifest)
    run_dir = resolve_run_dir(manifest_path, args.run_dir)

    if args.command == "notify-overflow":
        return notify_overflow(run_dir)
    if args.command == "status":
        return status_command(run_dir)
    try:
        return run_service(manifest_path, run_dir)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"[adannu] {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
