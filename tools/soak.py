#!/usr/bin/env python3
"""10-minute A/B soak for TEXTSTORE v0.3.1.

Measures p99 fsync, write p95/p99 end-to-end and read p99 under an open-loop
arrival schedule.  A profile that fails its threshold must be removed from the
SLO until it passes; this tool reports, it does not bless.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from massaru.store import Store

PROFILES = {
    "A": {"rate": 20.0, "write_p99": 150.0, "write_p95": 100.0, "window": 0.010},
    "B": {"rate": 50.0, "write_p99": 250.0, "write_p95": 180.0, "window": 0.010},
}


def p99(values: list[float], quantile: float = 0.99) -> float:
    if not values:
        return 0.0
    return sorted(values)[max(0, int(len(values) * quantile) - 1)]


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
            future = executor.submit(
                store.put, key, {"spec": {"n": counter}}, 0
            )
            kind = "write"

        def done(fut, sent=sent, kind=kind):
            latency = (time.perf_counter() - sent) * 1000.0
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"error": repr(exc)}))
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
    store.close()
    result = {
        "profile": profile,
        "durationSeconds": duration,
        "rate": spec["rate"],
        "fsyncP99Ms": round(p99(fsyncs), 3),
        "writeP95Ms": round(p99(writes, 0.95), 3),
        "writeP99Ms": round(p99(writes), 3),
        "readP99Ms": round(p99(reads), 3),
        "writes": len(writes),
        "reads": len(reads),
        "fsyncs": len(fsyncs),
    }
    result["pass"] = (
        result["writeP99Ms"] <= spec["write_p99"]
        and result["writeP95Ms"] <= spec["write_p95"]
        and result["readP99Ms"] <= 50.0
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--warmup", type=float, default=10.0)
    parser.add_argument("--store", default=None)
    args = parser.parse_args()
    path = Path(args.store) if args.store else Path(tempfile.mkdtemp()) / "store"
    result = run(args.profile, args.duration, args.warmup, path)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
