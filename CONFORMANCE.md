# TEXTSTORE v0.2 conformance gate

A PR that touches `massaru/store.py`, `massaru/store_fsck.py`, `nqctl` or the
API schema is not mergeable until every gate below is green.  Results must
be commands + exit codes, not claims.

## 1. Automated gate

```sh
python3 -m pytest -rA
python3 -m pytest --cov=kernel.libbu --cov=agents --cov=massaru --cov=nqctl \
  --cov-fail-under=70 -q
python3 tools/lint.py
python3 kernel/mutation_run.py
python3 agents/tests/mutation_run.py
python3 tests/v1/mutation_run.py
```

Required: all tests pass; coverage ≥ 70%; lint clean; TEXTSTORE mutants
≥ 12/12 (currently 17/17: fsync, prev, rev, hash, CAS, meta, anchor,
watch 410, limits, fail-stop, flock).

## 2. Invariant scenarios

| Scenario | Required result |
|---|---|
| `put`/`delete` with stale `resourceVersion` | exactly one success, rest `409` |
| parallel writers | second `EBUSY`; no interleaved lines |
| crash after write before fsync | batch truncated; store read-only; `rev` unchanged |
| crash after fsync before response | transaction durable; restart reads it |
| ENOSPC/EIO | ftruncate to `batchStartOffset`; read-only until clean `fsck` |
| torn tail (no `\n`) | trimmed to last valid line; repair audited |
| corrupt middle / prev / rev / hash | `fsck` FAIL; start refused; restore required |
| truncation below anchor | `fsck` FAIL |
| watch 100 watchers, 1 slow | slow gets `410`; others complete; zero duplicates |
| 1 000 writes during watch | zero losses/duplicates |
| value 1 MiB+/line 2 MiB+ | `413`; no journal line |
| JCS golden vectors | byte-for-byte expected |
| restore snapshot + segments/copy | `rev`, `hash`, state match |
| SLO | read p99 ≤50 ms, write p99 ≤150 ms at ≤50 rps |

## 3. Documents

- `INVARIANTS.md` is normative for durability/fail-stop/watch.
- `API_SCHEMA.md` is normative for rev/resourceVersion, generation,
  Lease, 410, dry-run and pagination.
- `TEXTSTORE_v0.2.md` (docs/architecture) is the owner spec; this repo’s
  documents must not contradict it.

## 4. Sign-off

- Implementer: tests/commands above.
- Reviewer: independent diff read + at least one corruption drill.
- Owner: accepts only with measured results and no open `block`.
