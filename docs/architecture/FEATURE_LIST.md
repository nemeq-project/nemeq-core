# Nemeq — Feature List (post-v1)

**Дата:** 2026-11-23 · **Статус:** канон future-фич · **Решение:** 23.11 — crypto at rest отложено; v1 без шифрования at-rest (sealed = ACL/FS; root trusted).
**Правило:** фича попадает сюда, если сознательно **не делаем в v1**; у каждой — зачем, зависимости, риск.

## 1. Что в v1 (не future)

- 4 зоны, zi/su образы, epishu, RBAC (admin global root / admin_epishu / ops / viewer), группы epishu+users.
- zikru-su по пользователю, sealed B (ACL), zikru-zi read-only для ops, авто-дистилляция (PII→блок, k<3→quarantine, secret→sealed).
- Consent revocation (sync tombstone + async purge), audit (root вне модели + syslog TLS anchor), classification (service sets, model propose, denylist).
- Sessions: одна активная, safe-point preemption, context store, read-after-sync, adannu ops_system.
- Grants: allow-only, global deny, TTL, grant_check, janitor.
- Метрики токенов/стоимости, VM/Grafana/MCP.

## 2. Future (F-01…)

| ID | Фича | Зачем | Зависит | Приоритет | Риск/заметка |
|---|---|---|---|---|---|
| **F-01** | **Crypto at rest:** envelope encryption, один root-owned decrypt service, 1 DEK/epishu, KEK custody, rotation/recovery drill, crypto-shred бэкапов | защита от не-root компрометации, утечки бэкапов; физическое удаление | keyring, decrypt service | **P1** (первая future) | v1: sealed = ACL/FS; root trusted; без at-rest шифрования. Принять риск явно |
| F-02 | Per-record crypto-shred: per-segment DEK | точечное удаление записей/сегментов | F-01 | P2 | сложность keyring/rotation |
| F-03 | Обмен знаниями между epishu | совет/кооперация | consent, governance | P2 | утечки ПД между личностями; только явные артефакты |
| F-04 | Миграция Kalam ↔ ядро | перенос инкарнаций | epoch/fencing, capability reissue | P2 | двойная запись, stale epoch |
| F-05 | Embeddings/semantic recall для Nemeq | поиск по памяти | index | P3 (дальнее) | цена/VRAM; сейчас не используем |
| F-06 | WORM/immutable audit anchor | усиление tamper-evidence | syslog TLS уже есть | P2 | root может подавить экспорт; WORM позже |
| F-07 | Marketplace/продажа персон | бизнес | лицензии, право | P3 | бизнес/юридика |
| F-08 | Multi-Kalam UI/management | несколько инсталляций | Kalam API | P3 | scope |
| F-09 | HA/distributed sessions | отказоустойчивость | session store | P3 | сложность |
| F-10 | ML-классификация / LLM-judge | лучше PII | classifier | P2 | false positives/negatives; denylist пол |
| F-11 | Consent/DSAR автоматизация на запись | per-record rights | F-01/F-02 | P2 | юридика |
| F-12 | Backup/restore drills автоматизация | RPO/RTO | backup | P2 | ops |
| F-13 | Public API/SDK | интеграции | API | P3 | безопасность |
| F-14 | Billing/pricing per epishu | бизнес | usage | P3 | бизнес |
| F-15 | Model/provider plugins | новые модели | gateway | P3 | supply chain |
| F-16 | Audit UI/query для viewer | наблюдаемость | audit | P2 | не раскрывать ПД |
| F-17 | UI control plane (web) | управление Nemeq | API | P1 | отдельный проект; ocmanager как база |

## 3. Явно не делать

- Quorum/2-of-N для root (root trusted).
- Вложенные группы и deny-гранты (allow + глобальный deny-list).
- ML-guard prompt-injection как единственная защита (нужны safe points/tool allowlist).
- Шифрование at-rest в v1 (F-01).

## 4. Решения 23.11

- 1A consent: sync tombstone + async purge.
- 2C audit: root вне модели + syslog TLS anchor.
- 3 → F-01 (crypto at rest отложено).
- 4A classification: service sets, model propose, denylist, fail-closed.
- 5A sessions: safe point, state machine, sync_token, одна на (principal, client_id).
- 6A k-support: accountable_root + 3 distinct principal+subject + evidence.
