# TEXTSTORE v0.2 conformance gate

A PR that touches `massaru/store.py`, `massaru/store_fsck.py`, `nqctl` or the
API schema is not mergeable until every gate below is green.  Results must
be commands + exit codes, not claims.

## 1. Automated gate

```sh
python3 -m pytest -rA
python3 -m pytest --cov=kernel.libbu --cov=agents --cov=massaru --cov=nqctl \
  --cov=apiserver --cov-fail-under=70 -q
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
python3 tools/mutation_p0.py     # 37/37
python3 -m pytest tests/v1/test_one_million_replay.py -m slow -q -rA -s -p no:cacheprovider
```

Required: admission chain (validation → defaults → RBAC → quota → v1 read-only
deny with AUDIT_DENY advancing rev), `/status` subresource limited to
`epishus|sessions|quotas`, RFC 7807 problem bodies, `nqctl apply/get/describe/watch`
over HTTP while local `nqctl store fsck|verify|snapshot` keeps working.

### 6.1 Status code contract (normative)

| Situation | Code |
|---|---|
| CAS `resourceVersion` mismatch (stale or bogus token) | `409 Conflict` (never 410) |
| Another writer holds the store lock (`StoreBusy`) | `409 Conflict` |
| Watch subscriber buffer overflow (`WatchGone`) | `410 Expired` (relist) |
| Missing/empty actor, or actor without a covering Grant | `403 Forbidden` + `AUDIT_DENY` |
| Body > 2 MiB / value > 1 MiB | `413 TooLarge` |
| Schema-invalid spec | `422 Invalid` |

CAS `resourceVersion` mismatch → `409`, never `410`. `StoreBusy` (another
writer holds `flock`) also → `409`. The only confirmed `410` is `WatchGone`
(buffer overflow), never `409` for it. Covered by
`tests/p0/test_routes_watch.py::test_watch_overflow_returns_410`,
`tests/p0/test_api_cas.py`, and mutant `P34` (`WatchGone` mapped to 409).

### 6.2 RBAC matrix (normative)

| Operation | Verb | Where enforced |
|---|---|---|
| `GET /{plural}` | `list` (verb gate, then per item scope filter) | collection + item |
| `GET /{plural}/{name}` | `get` | object scope |
| `POST /{plural}` | `create` + admission | object scope |
| `PUT /{plural}/{name}` | `update` | object scope |
| `PUT /{plural}/{name}/status` | **`status`** (not `update`) | object scope |
| `DELETE /{plural}/{name}` | `delete` | object scope |
| `GET ?watch=1` | `watch` (verb gate, then per event scope filter) | collection + event |

An empty actor is denied before any object lookup. An actor holding only
`get` is denied `list`/`watch` with `403`. Role verbs are a ceiling: a
non-empty `spec.verbs` narrows it and cannot escalate (`viewer` + `delete`
is still denied); `status` is granted only to `ops`/`admin_epishu`/`admin`
and only explicitly. Denied reads outside scope write an `AUDIT_DENY` with a
**redacted** name.

Covered by `tests/p0/test_authz.py`, `tests/p0/test_collection_gate.py`,
`tests/p0/test_rbac_ceiling.py`, `tests/p0/test_admission_escalation.py`.

### 6.3 Watch watermark

`GET ?watch=1&fromRev=N` returns `metadata.nextRev`, the global `rev` of the
last inspected event (including other resources and scope-filtered events).
Empty backlog returns `nextRev == fromRev`. Same-`rev` groups are never split
by `limit`. Covered by `tests/p0/test_watch_page.py`; mutants `P27`, `P28`,
`P29`.

### 6.4 Deferred blockers (not implemented, not silently added)

| Blocker | Why deferred | What it would require |
|---|---|---|
| Opaque signed resume token (`watch`) | Contract v1 uses numeric `fromRev` + `metadata.nextRev`; a token needs a key, rotation and a client contract | New API field, secret management, migration of `nqctl` |
| Retention TTL / journal compaction | `Store` keeps the full journal; there is no deletion path, so an age cutoff would be arbitrary and unverifiable | Compaction design, anchor interaction, restore-drill changes |

Until these land, the **only** `410` is `WatchGone` (buffer overflow, replay
impossible), and expired-cursor semantics must not be claimed in docs or tests.

### 6.5 Port binding contract

`make_server(port=None)` → `DEFAULT_PORT` (8448); `make_server(port=0)` → an
ephemeral port (tests must never bind 8448 or collide with a parallel run).
`serve(port=None)` → `port_from_env()`. Verified by
`tests/p0/test_server.py::test_make_server_binds_ephemeral_port` and mutant
`P33`.

### 6.6 1M replay/fsck acceptance (measured 2026-11-25)

**Covered by a dedicated CI job.** `.gitea/workflows/ci.yml` defines
`slow-acceptance` (`timeout-minutes: 30`) which runs the test explicitly:

```sh
python -m pytest tests/v1/test_one_million_replay.py -m slow -q -rA -s -p no:cacheprovider
```

The main `test` job keeps `addopts = -m 'not slow'`; the explicit `-m slow`
in this job overrides the default selection so the test really executes.
Timing budget: **journal generation ~35 s + replay/fsck <= 120 s** (asserted
inside the test; the 60 s figure in §8 is the target, not the gate).

`tests/v1/test_one_million_replay.py` (marked `slow`, excluded from the default
run via `addopts = -m 'not slow'`) builds a real 1 000 000-revision journal and
runs replay + fsck over it:

```
python3 -m pytest tests/v1/test_one_million_replay.py -m slow -q -s
1M replay 68.6s, seed 34.6s
1 passed                                                        rc=0
```

Result: `ok=true`, `transactions=1_000_000`, `state objects=1_000_000`,
`last_hash` chained, journal 755 MB, replay 67.7-68.6 s (SLO: <=60 s target,
<=120 s gate). Status: **passes the 120 s gate, above the 60 s target** —
recorded honestly, no threshold tuning.

### 6.7 CI gate status (blocker, not cosmetic)

All PR #20 commits (`bdefa67`, `748e841`, `c1b2de6`, `df87525`, `23866d0`)
carry `ci / test (push)` and `ci / test (pull_request)` statuses stuck in
`pending — Waiting to run`; Gitea shows **30 queued runs, 0 executed** on
192.168.96.51 because no `act_runner` is registered (issue #16 item 7).

Consequence: the workflow in `.gitea/workflows/ci.yml` has never actually run.
Local numbers (pytest/coverage/lint/4 mutation suites) are the only evidence
so far. This is an **acceptance blocker**, tracked here until a runner is
registered and head shows green statuses.

### 6.8 SLO soak status (outside SLO)

`SOAK.md` records both A/B profiles **failing** their write-latency gates
(A writeP99 158.9-193.3 ms vs 150; writeP95 117.6-161.0 ms vs 100).
The soak is **not** a passed gate; it is listed as an open blocker pending
retuning (rps/window) and re-measurement on the target disk.
