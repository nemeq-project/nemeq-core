# TEXTSTORE v0.3.1 — ext4 soak result (2026-11-24)

Command (store on ext4, not tmpfs):

```sh
python3 tools/soak.py --profile A --duration 600 --warmup 10 \
  --store /home/daedalus/work/soak-v031/A2
python3 tools/soak.py --profile B --duration 600 --warmup 10 \
  --store /home/daedalus/work/soak-v031/B2
```

`fsType=ext4`; store path refused on tmpfs (exit 2). One journal fsync per
commit: fsyncs == commits (A 8107/8107, B 6548/6548). No meta/state/view/
anchor fsync is before the response.

## Profile A — 20 rps, window 10 ms

| metric | value |
|---|---:|
| p99 fsync | 99.410 ms |
| write p95 | 161.019 ms |
| write p99 | 193.263 ms |
| read p99 | 22.498 ms |
| writes / commits | 9600 / 8107 |
| avg batch | 1.184 |
| max batch | 3 |
| pass | **false** |

## Profile B — 50 rps, window 10 ms (stricter than spec 25 ms)

| metric | value |
|---|---:|
| p99 fsync | 106.139 ms |
| write p95 | 255.079 ms |
| write p99 | 309.224 ms |
| read p99 | 79.170 ms |
| writes / commits | 24000 / 6548 |
| avg batch | 3.665 |
| max batch | 16 |
| pass | **false** |

## Verdict

Both A and B **fail their SLO thresholds on this host**. The old numbers
(fsync≈0) were invalid because the default was `/tmp` (tmpfs). The critical
path is now one journal fsync; remaining latency is fsync (p99 99–106 ms)
plus group-commit queueing. Per TEXTSTORE v0.3.1, a profile that fails its
soak is removed from the SLO until it is tuned (rps/window) and re-measured;
it is not blessed here.

Histograms are emitted in the JSON output (`writeHistogram`,
`readHistogram`, `fsyncHistogram`); raw files: `/tmp/soakA2_ext4.json`,
`/tmp/soakB2_ext4.json` (local run artifacts).
