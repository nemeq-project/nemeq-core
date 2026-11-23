# Nemeq — Architecture (v1, read-only)

**Дата:** 2026-11-23 · **Статус:** v1 (read-only expert council). Детали — SDD v0.4 + addenda §1–12.

## 1. Слои

```
5 Приклад: CLI/HTTP/UI/дашборд
4 Nemeq (harness): Awilu (zi+su), Subur (init/reu/adannu/capd/massaru/llm-gateway), Libbu
3 Runtime: Python/asyncio (Rust — план)
2 Linux: systemd, cgroups v2, namespaces, pidfd, seccomp, LSM; LXD
1 Железо
```

## 2. Зоны (4)

`/proc` (live, read-only) · `/run` (volatile) · `/var` (mutable: epishu/state/data) · `/opt` (immutable образы).

## 3. zi/su/epishu

- zi: `manifest.json`, `anima.toml`, `skills/`, `knowledge/`, `style/`, `zikru-zi/`; digest; read-only.
- su: `manifest.json`, `body.toml`, `tools/`; digest; read-only.
- epishu = zi ∪ su overlay; `instance.toml` (refs+digests, lease), `tush.toml`, `paqidutu/`, `zikru-zi/`, `zikru-su/`, `view/`.

## 4. Память

WAL (himem), atoms, index (json+fts+hot), claims, traces; zikru-su по пользователю; zikru-zi общий; read-after-sync (fsync/commit_seq); sealed = ACL/FS (v1); raw off.

## 5. RBAC

admin (global root) / admin_epishu / ops / viewer; группы epishu+users; allow-only + глобальный deny; sealed ACL (admin_epishu 403; root direct).

## 6. Сессии

Одна активная на epishu; очередь; safe-point preemption; context store; sync_token/epoch fencing; adannu ops_system.

## 7. LLM/метрики

llm-gateway/awilu-egress; lease (tokens, cost, cache); token metrics cached/uncached/output/reasoning; quotas installation+epishu; VM/Grafana.

## 8. v1-границы

Нет внешних N-sink, exactly-once/restore/DSAR/crypto — v2. LLM-вызовы read-only. См. `V1_SCOPE.md`.
