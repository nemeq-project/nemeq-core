# Nemeq v1 — Implementation Plan (handoff)

**Дата:** 2026-11-23 · **Исполнитель:** Дедал · **Стенд:** `c-nemeq` .46 (10.238.183.32); метрики .30.

## Фазы

1. **Spec freeze:** merge PR #2; v1 scope read-only (`V1_SCOPE.md`).
2. **Каркас:** ветка `feature/v1-core`; CI (pytest, мутанты, lint); `riksu.toml`.
3. **massaru:** install/run/sleep/stop/commit/list/inspect; zi/su digest; instance layout; bind_model.
4. **llm-gateway/awilu-egress:** read-only вызовы; lease/квоты; ключи вне Awilu; usage ledger.
5. **Память:** WAL/atoms/index/claims/traces; read-after-sync; zikru-su по пользователю; sealed ACL.
6. **Сессии:** одна активная, очередь, context store, sync_token/epoch; adannu ops_system.
7. **RBAC:** admin/admin_epishu/ops/viewer; группы; grant_check.
8. **3 Awilu:** Октавия/Лира/Ада; 3 zi/su; qatum; прогон совета.
9. **Метрики:** exporter live → VM; дашборды; алерты.
10. **UI (F-17):** каталог/сессии/метрики (по готовности).

## Первые задачи (порядок)

1. `feature/v1-core` + CI + скелет massaru.
2. Instance layout + digest + bind_model (тест на 1 epishu).
3. llm-gateway read-only + usage ledger (тест: ключи вне Awilu).
4. 3 Awilu на c-nemeq; прогон `challenge → 3 review`.
5. Метрики live; дашборд.

## Приёмка

- `V1_SCOPE.md` §4; тесты T-01…T-34 + RB/RS v1; мутанты; 3 review; метрики.
- Внешние side effects/DSAR/crypto — **не в v1**; если код их добавляет — ревью Луча/Гермеса и v2.

## Делегирование

Простые куски (тесты-скелеты, докстринги, фикстуры, переименования) — Умману через `ummanu-dev`; архитектура/epoch/CAS/WAL/security — только Дедал; приёмка — Верста + Луч.
