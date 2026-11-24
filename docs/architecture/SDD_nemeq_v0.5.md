# SDD Nemeq v0.5 — консолидированная спецификация v1

**Дата:** 2026-11-24 · **Статус:** консолидированная (заменяет SDD v0.4) · **Владелец:** Верста
**Основание:** SDD v0.4 + D-11 (apiserver) + TEXTSTORE v0.3.1 + EVAL v0.2 + V1_SCOPE.
**Принцип:** детали — в профильных документах; здесь — целостная картина и границы.

---

## 1. Назначение и границы v1

**v1 — read-only экспертный совет.** Три Awilu (Октавия/Opus, Лира/Sonnet, Ада/GPT) работают в Libbu: сессии, память, права, метрики. LLM-вызовы read-only, внешних необратимых эффектов нет.

**Не входит в v1:** sink/outbox/exactly-once/restore, полный DSAR, crypto at rest, PII, cross-epishu exchange, marketplace. Всё это — v2 (F-18 baseline rc11).

---

## 2. Термины

| Термин | Значение |
|---|---|
| **Libbu** | ядро: apiserver + встроенный admission + базовые контроллеры |
| **Subur** | служебный epishu без zi: massaru, session, gateway, gc; `kind=subur` |
| **Awilu** | целевой epishu (zi ∪ su): эксперт в совете |
| **zi** | образ души: skills, knowledge, style, zikru-zi; immutable, digest |
| **su** | образ тела: tools, capabilities, body.toml; immutable, digest |
| **epishu** | инстанс (zi ∪ su overlay), единственное место жизни |
| **zikru-zi / zikru-su** | память о себе (без ПД) / память о пользователе (ПД только здесь) |
| **qatum** | реестр прав на инструменты |

---

## 3. Слои

```
5  Приклад: nqctl, ocmanager UI, Grafana
4  Nemeq: Libbu (apiserver+admission), Subur (контроллеры), Awilu (epishu)
3  Runtime: Python asyncio (Rust — цель переписывания)
2  Linux: systemd, cgroups v2, namespaces, seccomp/LSM, LXD
1  Железо
```

---

## 4. Топология и endpoints

| Компонент | Где | Endpoint |
|---|---|---|
| apiserver | c-nemeq (10.238.183.32) | `https://192.168.96.46:8448` (mTLS) |
| ocmanager UI | abuntu .51 | `https://192.168.96.51/` |
| nqctl | рабочая станция | `~/.nq/config` → 8448 |
| llm-gateway | c-nemeq | `:8080` internal |
| exporter | c-nemeq | `:9101` |
| VictoriaMetrics / Grafana | gsrv .30 | `:8428` / `:3000` |
| Gitea / OpenCloud | amgm / abuntu | `:8090` / `:8092` |

Схема: `docs/architecture/ARCH_SERVICES_v0.1.md`, `nemeq_services.png`.

---

## 5. Control plane

- **Единственный путь изменений** — apiserver; прямые записи «компонент → диск» и «компонент → компонент» запрещены.
- Ресурсы `nemeq.io/v1alpha1`: `Zi`, `Su`, `Epishu`, `Session`, `Grant`, `Quota`, `Model`, `Effect` (схема; v1 admission deny).
- Семантика: `spec/status`, `resourceVersion` для CAS (`<rev>-<uid8>`), глобальный `rev` для watch/bookmarks, `410 Expired`, `generation/observedGeneration`, `Lease`, finalizers, `dryRun`, пагинация, field/label selectors.
- Admission: validation → defaults → authz → quota → policy. Policy v1: deny внешних эффектов и write-capability.
- Audit: каждая запись (включая deny) в журнал; hash-chain + якоря.
- Истина — объект в API; файлы — материализация.

Детали: `APISERVER_v0.4.md`.

---

## 6. Store

`TEXTSTORE v0.3.1`: append-only JSONL + батч-фрейминг (commit-строка), hash-chain, Ed25519-якоря + off-host watermark, `committedOffset` из журнала, `meta` — кэш.

Инварианты: один писатель (flock), CAS под локом, fsync до ответа, неполный батч не виден, усечение ниже watermark → FAIL, watch ровно один раз / 410 при отставании.

SLO — **только после 10-мин soak**: read p99 ≤50 мс; write профиль A ≤20 rps/p99 250 мс (target 200), p95 180 мс; профиль B (50 rps) — диагностика, вне SLO (замер 309 мс).

---

## 7. Runtime

- `/opt/nemeq/{zi,su}/<name>@<version>/` — immutable, digest, symlink запрещён.
- `/var/nemeq/epishu/<id>/` — `instance.toml`, `tush.toml`, `paqidutu/`, `zikru-zi/`, `zikru-su/`, `view/`, генерируемые MD.
- massaru — контроллер `Epishu`: чистый `reconcile(spec,status) → plan`, затем материализация; lifecycle через API, не CLI.
- Сессии: одна активная на epishu, очередь в `status`, Lease, safe-point preemption, `syncToken/epoch`.
- Сон/пробуждение: `paqidutu`, WAL replay, commit идемпотентен.

Детали: `02b-anima-su-layout.md`, `ADDENDUM_sessions_v0.1.md`.

---

## 8. Память

- `zikru-zi` — самознание, уроки, стиль; без ПД.
- `zikru-su` — ПД пользователя; при потере тела теряется (кроме бэкапа).
- `data/epishu/<id>/`: atoms, wal, claims, settlement, traces, usage; append-only.
- `state/<id>/`: memory-index, fts, hot, checkpoint — atomic replace.
- read-after-sync; hard-delete/TTL; sealed ACL; classification наследуется на chunks/summary/embeddings.
- Embeddings — не в v1 (feature list).

Детали: `ADDENDUM_rbac_v0.1.md` §память, SDD Anima core.

---

## 9. RBAC и tenancy

- Роли: `admin` (global root), `admin_epishu`, `ops`, `viewer`.
- `Grant`/`Quota` — объекты API; `scope` = epishu/tenant; allow-only + глобальный deny.
- `admin_epishu` управляет своим epishu, не читает sealed; `root` — прямой доступ с аудитом.
- Read gate: per-agent uid/LSM, запрет ptrace/proc/mem/SCM_RIGHTS/memfd/io_uring; fail-closed при отказе.

---

## 10. LLM gateway и метрики

- llm-gateway — единственный egress к провайдерам; read-only; ключи вне Awilu.
- `Model`/`Quota` — объекты API; lease/квоты; usage ledger: cached/uncached/output/reasoning, cost.
- Метрики: exporter → VictoriaMetrics → Grafana; без промптов и ПД.

---

## 11. UI и nqctl

- **nqctl** — CLI без демона: `apply/get/describe/logs/watch`, `store fsck|verify|snapshot` (локально).
- **ocmanager** — веб-UI: каталог epishu, сессии, права, audit, метрики; только через API.
- Отдельного портала нет.

---

## 12. Оценка и приёмка

`EVAL_PROTOCOL` (L0–L7):

- **L0** Libbu: 17 инвариантов + 10 критических мутантов + авто ≥90%/класс.
- **L1** жизненный цикл: 5 сценариев.
- **L2** паспорт модели: 10 заданий × 3 повтора.
- **L3** пилот 20 задач × 3: исполняемые гейты, known-groups, false-done ≤10%, без рейтингов.
- **L4** совет: сигнал (β, effective votes, compute-matched baseline), решение отложено.
- **L6** безопасность: IPI/exfil/PII; ASR ≤5%, utility ≥90%, PII 0.
- **L7** validity ledger, rater agreement.

Store-приёмка: conformance из TEXTSTORE v0.3.1 (batch framing, anchor, ENOSPC/EIO, 100 watchers, restore, soak).

---

## 13. Безопасность и изоляция

- Read-only — свойство ядра: инструмент с внешним эффектом не регистрируется; egress только через gateway.
- Секреты — `secrets/` 0700 + `secretRef`; в журнал/логи не попадают.
- Read gate, sealed ACL, аудит каждого доступа; отказ при недоступности механизма.
- Threat model: `nemeq_textstore_review_20261124/erra_threat_model.md`.

---

## 14. Деплой, бэкап, RTO

- c-nemeq на amgm; systemd-юниты apiserver/controllers/gateway; LXD-proxy 8448.
- Бэкап: журнал + снапшоты + якоря; off-host копия; restore-drill.
- RPO: 0 для подтверждённых записей; при потере диска — интервал бэкапа.
- RTO ≤15 мин — только после drill; иначе не заявлять.
- Один узел в v1; HA — репликация журнала (NATS/etcd) за интерфейсом `Store`.

---

## 15. v2 (заморожено)

Effect protocol F-18 (rc11 baseline): sink T/I/N, outbox, exactly-once, restore FSM, DSAR, crypto. Реализуется отдельной линией; v1 admission запрещает `Effect`.

---

## 16. Открытые вопросы

- `INVARIANTS.md`, `API_SCHEMA.md`, `CONFORMANCE.md` — до P0 (issue #16).
- CI runner Gitea Actions.
- SLO soak на целевом диске.
- SDD v0.5 ратификация: Луч + Гермес + один внешний.

---

## 17. История

v0.4 → v0.5: добавлены D-11 (apiserver), TEXTSTORE v0.3.1, EVAL v0.2, топология/endpoints, приёмка store, v2-границы. Детали вынесены в профильные документы.
