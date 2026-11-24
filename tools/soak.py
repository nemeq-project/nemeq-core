#!/usr/bin/env python3
"""10-minute A/B soak for TEXTSTORE v0.3.1.

This tool refuses non-ext4 stores: /tmp is tmpfs on this host and fsync=0
would make the measurement meaningless.  It reports p99 fsync, p95/p99
end-to-end, p99 read, histograms and group-commit batch size.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from massaru.store import Store

PROFILES = {
    "A": {
        "rate": 20.0,
        "write_p99": 150.0,
        "write_p95": 100.0,
        "window": 0.010,
    },
    "B": {
        "rate": 50.0,
        "write_p99": 250.0,
        "write_p95": 180.0,
        "window": 0.010,  # stricter than the spec's 25 ms B window
    },
}
BUCKETS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000)


def _unescape_mount(value: str) -> str:
    return (
        value.replace("\\040", " ").replace("\\011", "\t")
        .replace("\\012", "\n").replace("\\134", "\\")
    )


def fs_type(path: Path) -> str:
    target = str(path.resolve())
    best_mount = ""
    best_type = "unknown"
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        mount = _unescape_mount(fields[1])
        fstype = fields[2]
        if mount == "/" or target == mount or target.startswith(
            mount.rstrip("/") + "/"
        ):
            if len(mount) >= len(best_mount):
                best_mount = mount
                best_type = fstype
    return best_type


def pct(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    return sorted(values)[max(0, int(len(values) * quantile) - 1)]


def histogram(values: list[float]) -> dict[str, int]:
    result: dict[str, int] = {}
    for bucket in BUCKETS:
        result[f"<={bucket}"] = sum(value <= bucket for value in values)
    result[">60000"] = sum(value > 60000 for value in values)
    return result


def run(profile: str, duration: float, warmup: float, path: Path) -> dict:
    spec = PROFILES[profile]
    sink = path.parent / f"{path.name}-anchor-sink"
    store = Store(path, commit_window=spec["window"], anchor_sink=sink)
    fsyncs: list[float] = []
    original_fsync = store._fsync_fd

    def timed_fsync(fd: int) -> None:
        start = time.perf_counter()
        original_fsync(fd)
        fsyncs.append((time.perf_counter() - start) * 1000.0)

    store._fsync_fd = timed_fsync
    writes: list[float] = []
    reads: list[float] = []
    executor = ThreadPoolExecutor(max_workers=64)
    futures = []
    started = time.monotonic()
    deadline = started + warmup + duration
    next_tick = started + warmup
    counter = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now < next_tick:
            time.sleep(min(next_tick - now, 0.01))
            continue
        sent = time.perf_counter()
        counter += 1
        key = f"epishu/soak-{counter:08d}"
        if counter % 5 == 0:
            future = executor.submit(store.get, key)
            kind = "read"
        else:
            future = executor.submit(store.put, key, {"spec": {"n": counter}}, 0)
            kind = "write"

        def done(fut, sent=sent, kind=kind):
            latency = (time.perf_counter() - sent) * 1000.0
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"error": repr(exc)}), file=sys.stderr)
            else:
                (writes if kind == "write" else reads).append(latency)

        future.add_done_callback(done)
        futures.append(future)
        if len(futures) > 512:
            wait(futures[:256])
            futures = futures[256:]
        next_tick += 1.0 / spec["rate"]
    wait(futures)
    executor.shutdown(wait=True)
    store.flush()
    stats = store.stats()
    store.close()
    commits = max(1, stats["commits"])
    result = {
        "profile": profile,
        "durationSeconds": duration,
        "store": str(path),
        "fsType": fs_type(path),
        "windowMs": spec["window"] * 1000.0,
        "rate": spec["rate"],
        "fsyncP99Ms": round(pct(fsyncs, 0.99), 3),
        "writeP95Ms": round(pct(writes, 0.95), 3),
        "writeP99Ms": round(pct(writes, 0.99), 3),
        "readP99Ms": round(pct(reads, 0.99), 3),
        "writes": len(writes),
        "reads": len(reads),
        "commits": stats["commits"],
        "fsyncs": stats["fsyncs"],
        "maxBatch": stats["max_batch"],
        "avgBatch": round(stats["ops"] / commits, 3),
        "writeHistogram": histogram(writes),
        "readHistogram": histogram(reads),
        "fsyncHistogram": histogram(fsyncs),
    }
    result["pass"] = (
        result["fsType"] == "ext4"
        and result["writeP99Ms"] <= spec["write_p99"]
        and result["writeP95Ms"] <= spec["write_p95"]
        and result["readP99Ms"] <= 50.0
        and result["fsyncs"] <= result["commits"] + 2
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--store", required=True, help="ext4 store directory")
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--warmup", type=float, default=10.0)
    args = parser.parse_args()
    path = Path(args.store)
    path.mkdir(parents=True, exist_ok=True)
    fstype = fs_type(path)
    if fstype != "ext4":
        print(
            json.dumps(
                {
                    "error": "soak requires ext4 store; tmpfs is forbidden",
                    "store": str(path),
                    "fsType": fstype,
                }
            ),
            file=sys.stderr,
        )
        return 2
    result = run(args.profile, args.duration, args.warmup, path)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
