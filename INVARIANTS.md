# TEXTSTORE v0.3.1 — invariants (gate)

Normative for `massaru/store.py`, `massaru/store_fsck.py`, `nqctl store`.
A change that violates an invariant is a gate failure.

## 1. Source of truth and layout

- `log/*.jsonl` is the only source of truth. `state/`, `snap/`, `views/`,
  `meta.json` are derived/materialized; `meta` is a cache.
- A batch is N transaction lines followed by a **commit line**
  `{"type":"commit","batchId","fromRev","toRev","count","batchHash",...}`;
  the commit line is part of the hash-chain.
- Transaction hash covers `fmt/rev/prev/ts/actor/requestId/txn` with
  RFC 8785-style JCS. Commit hash covers its framing fields and `prev`.
- No SQL/LMDB/external store in v1.

## 2. Batch replay / integrity

- Transactions are buffered; they are applied **only** when a commit covers
  their `rev` range and `batchHash`. A valid prefix after the last commit
  without a commit is an **uncommitted batch**, not a valid suffix.
- On startup/`fsck --repair`, an uncommitted tail is truncated from the first
  pending transaction (`batchStartOffset`) and reported as repaired. A
  corrupt commit in the middle is `fsck` FAIL, never auto-applied.
- `committedOffset` is the end of the last commit; only transactions at or
  below it are visible to reads/watch/request-id dedup. `meta` may lag and
  never overrides the journal.
- `rev` starts at 1, never repeats; `prev` is the previous chain hash;
  commit lines carry the chain forward.
- Write failure handling: write all transaction lines + commit, fsync; on
  write/fsync/ENOSPC/EIO — `ftruncate(batchStartOffset)`, fsync, read-only
  fail-stop (5xx/507) until clean `fsck`/restore. `rev` does not move.

## 3. Durability / single writer

- One writer holds `flock` on `lock`; a second writer gets `EBUSY`.
- CAS is checked during batch build under the writer lock. N concurrent
  updates with one `resourceVersion` → exactly one success.
- Group commit window 2–10 ms; **one journal fsync per batch**; response only
  after that fsync.
- `meta`, `state/`, `views/`, automatic anchors and snapshots are **not** on
  the batch critical path. They are written by the maintenance thread after
  publication; `Store.flush()` waits for derived state. Snapshot/anchor are
  explicit maintenance operations.
- `requestId` dedup is rebuilt from committed transactions; an incomplete
  batch never contributes to dedup.

## 4. Anchors / off-host watermark

- Ed25519 anchors: `anchor/<rev>.anchor` =
  `{fmt,rev,hash,prevAnchorHash,keyId,sig}`. `prevAnchorHash` chains anchors;
  private seed is local 0600/HSM, public key/sink off-host.
- Anchor on snapshot/clean stop and every 60 s or 1 000 rev; off-host sink
  ack updates `anchor/state.json`:
  `{lastAnchorRev,lastAnchorHash,sinkAckRev,lag,updatedAt,status}`.
- Sink unavailable → `DEGRADED` + alert; local anchors continue. Writes are
  allowed until `max_anchor_lag` = 5 000 rev / 5 min, then 507 read-only.
- In `DEGRADED`, the remote truncation check is deferred until catch-up;
  local anchor/chain checks still run. `fsck --remote` explicitly checks the
  off-host watermark and fails if unavailable/inconsistent.
- Truncation below the latest local anchor or remote watermark is `fsck`
  FAIL, not a torn tail. A torn final line is the only auto-repair case.

## 5. Watch

- Subscribe first (register cursor), replay backlog, then live; dedup by
  `(rev, seq)`; publication only after fsync.
- Bounded per-subscriber buffer 10 000; overflow closes with `410 Gone` and
  a revision for relist; one slow watcher does not block the writer.

## 6. Format / API

- `fmt=1`; NFC; strict UTF-8; value ≤1 MiB; line ≤2 MiB → 413.
- `rev` is the global watch/bookmark revision; `resourceVersion` is
  `"<rev>-<uid8>"` and the CAS token. `generation` tracks spec changes;
  `status.observedGeneration` is controller-owned.
- `dryRun`, `limit/continue`, `Lease`, 409/410/413/422/503/507 per
  `API_SCHEMA.md`.

## 7. Soak / SLO (only after 10-minute ext4 soak)

`tools/soak.py --store PATH` requires `PATH` on **ext4**; `/tmp`/tmpfs exits
2. Soak records p99 journal fsync, p95/p99 end-to-end, read p99, histograms
and `commits/fsyncs/maxBatch/avgBatch`. A profile without a 10-minute ext4
run is outside SLO; a failing profile is removed, not excused.

| Metric | SLO |
|---|---|
| Read p99 (get/list) | ≤ 50 ms |
| Write p99 A | ≤ 150 ms at ≤20 rps, window 10 ms |
| Write p99 B | ≤ 250 ms at ≤50 rps, window 25 ms |
| Write p95 | ≤100 ms (A) / ≤180 ms (B) |
| Journal fsync p99 | reported per profile (no hidden fsyncs on path) |
| Replay 1M rev | ≤60 s (snapshot + tail) |
| fsck / fsck --remote 1M rev | ≤120 s / ≤180 s |

A profile without a 10-minute soak is **outside SLO**. If a profile fails,
lower rps/widen the window and re-measure; do not claim an unverified SLO.
Measured 2026-11-24 on ext4: **A/B fail** (A write p95/p99 161/193 ms,
B 255/309 ms; p99 fsync 99/106 ms). Both profiles are outside SLO until
retuned; see `SOAK.md`.
