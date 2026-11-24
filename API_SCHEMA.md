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
- A stale or expired CAS token is `409 Conflict`. A `410 Expired` is used
  when a watch cursor/continue token is no longer serviceable; the client
  must relist.
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
  Pagination is opaque; tokens are store-version scoped.
- `put(key, value, expected_rv, dryRun, requestId)` → object, or `409`.
  `dryRun` runs validation/CAS but performs no write and no rev advance.
- `delete(key, expected_rv, requestId)` → idempotent; writes a tombstone
  `DELETED` transaction.
- `watch(fromRev)` → events `{rev, seq, type, key, resourceVersion, value?}`;
  `ADDED/MODIFIED/DELETED`; dedup by `(rev, seq)`; bounded buffer 10 000,
  overflow `410 Gone`.
- `snapshot()` → rev; `fsck(repair=False)` → report.

## 5. Errors / status codes

| Code | Meaning |
|---:|---|
| 400 | invalid JSON/UTF-8/request |
| 409 | CAS conflict or `EBUSY` writer |
| 410 | watch/continue token expired; relist |
| 413 | value > 1 MiB or journal line > 2 MiB |
| 422 | semantically invalid resource |
| 503 | store fail-stop/read-only |
| 507 | insufficient storage; fail-stop |

Errors are RFC 7807-shaped (`type`, `title`, `status`, `detail`, `reason`).
Admission-denied requests are audited; no secret values are logged.

## 6. Authz and scope

- Every watch event and list item is authz-checked by scope, not only at
  subscription time.
- `Grant` scope is `epishu-id`/tenant; `nqctl` and `ocmanager` are clients,
  never direct store writers.

## 7. SLO

- Read p99 ≤ 50 ms; write p99 ≤ 150 ms at ≤ 50 rps; write p95 ≤ 100 ms.
- Replay 1M rev ≤ 60 s; fsck 1M rev ≤ 120 s.
- Group commit 2–10 ms, batch ≤ 1 000 records; backpressure `429`.
