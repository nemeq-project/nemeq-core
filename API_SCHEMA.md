# Nemeq control-plane API — schema contract (v1alpha1, gate)

This is the API contract for the P0 apiserver.  The store is a primitive;
clients see the HTTP semantics below, never JSONL files.

## 1. Envelope

```json
{
  "apiVersion": "nemeq.io/v1alpha1",
  "kind": "Epishu",
  "metadata": {
    "name": "octavia",
    "uid": "…",
    "resourceVersion": "42-1a2b3c4d",
    "generation": 3
  },
  "spec": { "...": "..." },
  "status": { "...": "..." }
}
```

- `metadata.name` + kind identify the object; store key is `kind/name`
  (the Store primitive uses lower-case resource prefixes internally).
- `metadata.uid` is immutable for the life of the object.
- `spec` is desired state (controller input); `status` is observed state
  (controller output only).
- Unknown `spec`/`status` fields are preserved forward-compatibly; breaking
  changes require `v1alpha2`, never journal rewriting.

## 2. `rev` vs `resourceVersion`

- `rev` is the global monotone journal revision (int). It is used by
  `watch(fromRev)` and bookmarks.
- `metadata.resourceVersion` is `"<rev>-<uid8>"`; it is the CAS token.
  A client sends it as `expected_rev` / `expected_rv`.
- **Stale CAS token → `409 Conflict`** (never `410`). `409` also legitimately
  means "another writer holds the store lock" (`StoreBusy`/`EBUSY`); CAS is
  not the only source of 409.
- **`410 Expired`** is returned in exactly one confirmed case in v1: a
  `watch` subscriber whose buffer overflowed (`WatchGone`). The stream is
  closed and the client must relist. `409` is never returned for that case.
  Rationale: overflowing the bounded buffer **drops events**, so the server
  can no longer prove the client's `fromRev` window is complete - replay is
  impossible, not merely old. This is not an age/TTL cutoff: the Store keeps
  the full journal (no retention/compaction in v1), so any `fromRev` below
  `head` is still replayable as long as the subscriber buffer holds.
- `continue` tokens are plain offsets into the already-filtered list; an
  invalid token is `400 BadRequest`, not `410`. There is no TTL, no HMAC
  cursor and no cursor secret in v1.
- **Deferred (not in v1):** an opaque signed resume token or a retention TTL
  would require a contract change and a Store compaction story; tracked as a
  separate blocker in `CONFORMANCE.md` §6.4, not implemented silently.
- Objects carry `generation` (spec changes); controllers write
  `status.observedGeneration` after observing that generation.

## 3. Resources

`Zi, Su, Epishu, Session, Grant, Quota, Model, Lease, Effect`.
`Effect` is declared only in v1 and is admission-denied (no external
effects). `Lease` is the controller/session leader lease:

```yaml
kind: Lease
spec: {holder: "session-1", durationSeconds: 15, renewTime: "..."}
status: {holder: "session-1", renewTime: "...", leaseDurationSeconds: 15}
```

One active session per Epishu is enforced by CAS on the Lease/Epishu
resourceVersion; leases are not a second source of truth.

## 4. Operations

- `get(key)` → object + `resourceVersion`.
- `list(prefix, labelSelector, limit, continue)` → `(items, next)`.
  `continue` is a plain decimal **offset into the already scope-filtered
  list** (not opaque, not store-version scoped). An invalid/non-numeric
  offset is `400 BadRequest`. Because filtering happens before slicing, a
  token never reveals how many objects exist outside the caller's scope, and
  a page never contains an out-of-scope item.
- `put(key, value, expected_rv, dryRun, requestId)` → object, or `409`.
  `dryRun` runs validation/CAS but performs no write and no rev advance.
- `delete(key, expected_rv, requestId)` → idempotent; writes a tombstone
  `DELETED` transaction.
- `watch(fromRev)` → events `{rev, seq, type, key, resourceVersion, value?}`;
  `ADDED/MODIFIED/DELETED`; dedup by `(rev, seq)`; bounded buffer 10 000,
  overflow `410 Gone`.
- HTTP `?watch=1&fromRev=N` (alias `from_rev`) is a **bounded page**, not a
  long-lived stream: it reads the backlog from the global journal, filters
  by this resource and the actor's Grant scope, and returns at most `limit`
  events. `fromRev` is always a numeric global `rev`.
- The response carries a **global watermark**:
  `{"items": [...], "metadata": {"nextRev": <int>, "fromRev": <int>, "limit": <int>}}`.
  `nextRev` is the `rev` of the last event the server actually inspected -
  including events for other resources and events filtered out by scope - so
  a client can resume with `fromRev=nextRev` even when `items` is empty or
  the page was filtered. With an empty backlog `nextRev == fromRev`.
- Resume: call again with `fromRev=metadata.nextRev`. Events of one `rev`
  (multiple `seq`) are never split by `limit`, so a transaction is observed
  atomically; filtered-out events still advance `nextRev`, therefore no gap
  is created. `object` is `null` for `DELETED`; the former value is used
  only internally for scope checks and is never exposed.
- `snapshot()` → rev; `fsck(repair=False, remote=None)` → report.
- `fsck --remote` checks the off-host Ed25519/watermark state; local
  anchors use `prevAnchorHash` chaining.
- A batch is visible only after its commit line; `committedOffset` is the
  end of the last commit.

## 5. Errors / status codes

| Code | Meaning |
|---:|---|
| 400 | invalid JSON/UTF-8/request |
| 409 | CAS conflict or `EBUSY` writer |
| 410 | watch subscriber buffer overflow (`WatchGone`); relist |
| 413 | value > 1 MiB or journal line > 2 MiB |
| 422 | semantically invalid resource |
| 503 | store fail-stop/read-only |
| 507 | insufficient storage or anchor-lag fail-stop |

Errors are RFC 7807-shaped (`type`, `title`, `status`, `detail`, `reason`).
Admission-denied requests are audited; no secret values are logged.

## 6. Authz and scope

- Every watch event and list item is authz-checked by scope, not only at
  subscription time.
- `Grant` scope is `epishu-id`/tenant; `nqctl` and `ocmanager` are clients,
  never direct store writers.

## 6a. P0 server surface

- `python -m apiserver [STORE] [--host H] [--port P]`; port default `8448`
  (`NEMEQ_API_PORT`), host default `127.0.0.1` (`NEMEQ_API_HOST`).
- Routes: `/healthz`, `/openapi.json`, `/metrics`,
  `/apis/nemeq.io/v1alpha1/{zis,sus,epishus,sessions,grants,quotas,models}`,
  `/{name}`, and `/{name}/status` for `epishus|sessions|quotas`.
- `?watch=1&fromRev=N&limit=L` (alias `from_rev`) returns
  `{"items":[...],"metadata":{"nextRev","fromRev","limit"}}` with
  `{type,rev,object}`; DELETED carries `object=null`.
- Actor comes from `X-Nemeq-Actor`; **an absent/empty actor is denied
  everywhere** (`403 Forbidden`), including `get`/`list`/`watch`/`delete`/
  `status`. There is no anonymous access in v1.
- RBAC is enforced on every operation: `get`, `list`, `watch`, `create`,
  `update`, `delete`, `status`. `list`/`watch` first require the verb itself
  (an actor with only `get` gets `403` for `list`/`watch`), then filter each
  item/event by the Grant scope.
- Role verbs are a **ceiling**. A non-empty `spec.verbs` narrows that
  ceiling and can never escalate: `viewer` + `["delete"]` still cannot
  delete, `viewer` + `["*"]` still cannot write. `status` is a separate verb
  granted only to `ops`/`admin_epishu`/`admin` and only when explicitly
  listed. An empty/absent `verbs` keeps the role defaults.
- Denials write an `AUDIT_DENY` entry; collection denials and out-of-scope
  reads are redacted (no object name).
- Denials write an `AUDIT_DENY` object (`audits/deny-N`) inside the journal
  (so it advances `rev` and enters the hash-chain). Denials for objects
  outside the actor's scope are **redacted**: the object name is not written.
- `nqctl` talks to this API (`--api-url`, `NEMEQ_API_URL`, `~/.nq/config`);
  `nqctl store ...` stays local.

## 7. SLO

- Read p99 ≤ 50 ms; write p99 ≤ 150 ms at ≤ 50 rps; write p95 ≤ 100 ms.
- Replay 1M rev ≤ 60 s; fsck 1M rev ≤ 120 s.
- Group commit 2–10 ms, batch ≤ 1 000 records; backpressure `429`.
