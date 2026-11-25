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
python3 tools/soak.py --profile A --duration 600 --warmup 10 --store /ext4/path/A
python3 tools/soak.py --profile B --duration 600 --warmup 10 --store /ext4/path/B
```

Required: all tests pass; coverage ≥ 70%; lint clean; TEXTSTORE mutants
≥ 12/12 (currently 18/18: fsync, prev, rev, hash, CAS, meta, anchor,
watch 410, limits, fail-stop, uncommitted batch).

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
| kill -9 in write before fsync | no transaction from the incomplete batch is visible; tail truncated |
| batch without commit | `fsck` FAIL; `--repair` truncates from batchStartOffset |
| Ed25519 anchor chain / watermark | signature, `prevAnchorHash`, `fsck --remote` checked |
| truncation below anchor | `fsck` FAIL |
| watch 100 watchers, 1 slow | slow gets `410`; others complete; zero duplicates |
| 1 000 writes during watch | zero losses/duplicates |
| value 1 MiB+/line 2 MiB+ | `413`; no journal line |
| JCS golden vectors | byte-for-byte expected |
| restore snapshot + segments/copy | `rev`, `hash`, state match |
| one fsync per batch | journal fsync count == commits; no meta/state/view/anchor fsync before response |
| group commit | 20 rps → avgBatch 1–2; maxBatch ≤1000 |
| soak filesystem | ext4 only; tmpfs exits 2 |
| SLO | **current status: A/B FAIL** — see `SOAK.md`; profiles outside SLO until retuned |

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

## 6. P0 apiserver conformance (feature/v1-apiserver-p0)

```sh
python3 -m pytest tests/p0 -q
python3 tools/mutation_p0.py     # 14/14
```

Required: admission chain (validation → defaults → RBAC → quota → v1 read-only
deny with AUDIT_DENY advancing rev), CAS 409/410, `/status` subresource limited
to `epishus|sessions|quotas`, watch filtered per resource, RFC 7807 problem
bodies, `nqctl apply/get/describe/watch` over HTTP while local
`nqctl store fsck|verify|snapshot` keeps working.
