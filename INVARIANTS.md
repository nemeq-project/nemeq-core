# TEXTSTORE v0.2 — invariants (gate)

This file is normative for `massaru/store.py`, `massaru/store_fsck.py` and
`nqctl store`.  A change that violates an invariant is a gate failure.

## 1. Source of truth and layout

- `log/*.jsonl` is the only source of truth. `state/`, `snap/`, `views/`,
  `meta.json` are derived/materialized and may be regenerated.
- One line = one atomic transaction. A line has
  `fmt,rev,prev,ts,actor,requestId,txn,hash` (exactly the format in
  `TEXTSTORE_v0.2.md` §2).
- No SQL, no LMDB, no external store in v1.

## 2. Integrity

- `hash = sha256(JCS({fmt,rev,prev,ts,actor,requestId,txn}))`; JCS is
  RFC 8785-style canonical JSON (sorted keys, UTF-8, no NaN/Inf).
- `prev` is the previous line's `hash`; the chain is global. `rev` starts at
  1 and never repeats.
- `fmt` is `1`; unknown formats are rejected.
- Every 1 000 revisions a signed anchor is written to
  `anchor/<rev>.anchor` (`{fmt,rev,hash,sig}`, HMAC-SHA256 with the
  apiserver key; the key and anchors must also be copied off-host).
- Truncation below the latest anchor is `fsck` FAIL, not a torn tail.
- A torn tail is only a final line without `\n`; it is trimmed by startup or
  `nqctl store fsck --repair`. A corrupt middle is never auto-repaired.

## 3. Write path / durability

- Exactly one writer holds `flock` on `lock`; a second writer gets `EBUSY`.
- A batch is built under the writer lock. CAS (`expected_rev` /
  `expected_rv`) is evaluated while building, not after writing.
- Each batch remembers `batchStartOffset`, writes all lines, then `fsync`.
  `committedOffset` and `rev` are published only after that fsync.
- On write/fsync/ENOSPC/EIO failure: `ftruncate(batchStartOffset)`, `fsync`,
  and the store enters **read-only fail-stop**; the client gets a 5xx, `rev`
  does not move. Only `fsck` (clean) / `fsck --repair` (torn tail) or restore
  clears read-only.
- Readers and watch see only transactions at/below `committedOffset` and
  only after publication.
- Group commit window is 2–10 ms; one fsync per batch; no acknowledgement
  before fsync.

## 4. CAS and API values

- `rev` is the global journal revision; `resourceVersion` is
  `"<rev>-<uid8>"` and is the CAS token for an object.
- `expected_rev` accepts a full `resourceVersion` (or a legacy integer
  rev); a mismatch is `409 Conflict`.
- `generation` increases when `spec` changes; `status.observedGeneration`
  is owned by the controller. `dryRun` validates/CAS but writes nothing.
- `requestId` dedup is reconstructed from the journal; a repeat in the
  window returns the same result and does not advance `rev`.

## 5. Watch

- Subscribe first (register cursor), then replay backlog, then live; no gap.
- Events are deduplicated by `(rev, seq)`.
- A subscriber buffer is bounded (default 10 000 events). Overflow closes
  the stream with `410 Gone` and a revision for relist; one slow watcher
  never blocks the writer or other watchers.
- `watch` is a read path: it never holds the writer lock while yielding.

## 6. Format limits

- Values are normalized to NFC before hashing/writing; invalid UTF-8 is 400.
- Value ≤ 1 MiB; journal line ≤ 2 MiB; larger is 413 and never enters the
  journal. Keys match `resource/name` with `name` `[a-z0-9][a-z0-9.-]{0,62}`.

## 7. SLO (v1 target; measured on target disk)

| Metric | SLO |
|---|---|
| Read p99 (get/list) | ≤ 50 ms |
| Write p99 (put/delete) | ≤ 150 ms at ≤ 50 rps |
| Write p95 | ≤ 100 ms |
| Replay 1M rev | ≤ 60 s (snapshot + tail) |
| fsck 1M rev | ≤ 120 s |
