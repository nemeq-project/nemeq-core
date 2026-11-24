# APISERVER v0.1 (заменён v0.2)

> **Внимание:** решения по store/языку/UI пересмотрены — см. `APISERVER_v0.2.md`. — централизованный API Nemeq (решение D-11)

**Дата:** 2026-11-24 · **Статус:** решение принято (CEO), проект на ревью · **Основание:** указание Повелителя — API должен быть централизован «как kube-apiserver».

---

## 1. Решение

Управляющая плоскость Nemeq — **один apiserver** с декларативными ресурсами, версионированием, watch и admission. Все компоненты (massaru, сессии, RBAC, llm-gateway, метрики) — **клиенты и контроллеры** этого API. Прямых записей «компонент → диск/другой компонент» больше нет.

**Не берём Kubernetes целиком.** Берём паттерн: ресурсы `spec/status`, `resourceVersion`, watch, admission, audit, optimistic concurrency. k8s/k3s, CRD-codegen, webhook-инфраструктура и HA-кластер в v1 не нужны.

---

## 2. Главное следствие: control plane ≠ data plane

| Плоскость | Что внутри | Через apiserver? |
|---|---|---|
| **Control** | Epishu, Zi, Su, Session, Grant, Quota, Model, Effect(declared) | **да**, единственный путь |
| **Data** | zikru-атомы, эмбеддинги, промпты, ответы LLM, метрики, логи | **нет**; свой API + токен + read gate |

Причина: etcd-подобный store не годится для блобов и высоких RPS; данные живут в своих сервисах, но **авторизация и учёт — из apiserver**.

Следствие для 02b-layout: **истина — объект в API**, а не файл на диске. `instance.toml`, каталоги epishu, состояние сессии — **материализация** контроллером. Раскладка остаётся контрактом, но не source of truth.

---

## 3. Минимальный API v1alpha1

`/apis/nemeq.io/v1alpha1/...`

| Ресурс | spec | status |
|---|---|---|
| `Zi` | image ref + digest, manifest | phase, verified |
| `Su` | image ref + digest, manifest, capabilities | phase, verified |
| `Epishu` | ziRef, suRef, model, quotas, kind(awilu/subur), readOnly | phase, node, sessionRef, conditions |
| `Session` | epishuRef, principal, clientId, priority, budget | phase(queued/active/done), queuePos, syncToken, epoch |
| `Grant` | subject, role, scope(epishu/tenant), verbs | — |
| `Quota` | scope, tokens, cost, concurrency | used, limit |
| `Model` | provider, modelId, context, price, capabilities | healthy, version |
| `Effect` | sinkClass, payloadHash, idempotencyKey | phase (v2; в v1 — только объявлен и запрещён admission) |

v1 работает с `Zi/Su/Epishu/Session/Grant/Quota/Model`. `Effect` — заготовка v2.

---

## 4. Семантика (то, что делает API «как kube-apiserver»)

1. **`resourceVersion`** — монотонная ревизия store. Любой update/delete требует предусловие; конфликт → `409 Conflict`. Никаких «последний победил».
2. **`watch`** — подписка с `fromRevision`, доставка `ADDED/MODIFIED/DELETED`, bookmarks, разрыв → relist. Компоненты не поллят.
3. **`spec/status`** — пользователь меняет `spec`; контроллер пишет только `status`. `status` — subresource.
4. **Admission-цепочка**: validation → defaults → authz (RBAC) → quota → policy → persist. Политика v1: **deny** любого ресурса, объявляющего внешний эффект или write-capability.
5. **Finalizers / deletionTimestamp** — удаление Epishu сначала останавливает сессию, потом снимает объект.
6. **Audit** — каждая запись: кто, что, before/after hash, requestId, revision. Один журнал вместо лоскутных логов; это же evidence для приёмки.
7. **Errors** — RFC 7807 + reason (`Conflict`, `Forbidden`, `Invalid`, `QuotaExceeded`).
8. **Idempotency** — create по имени идемпотентен (повтор → 409/тот же объект), delete идемпотентен. Для data plane остаётся `request_id`/`effect_id`.

---

## 5. Store

- **v1:** SQLite WAL (или Postgres) с таблицами `objects`, `revisions`, `changes`; writer один, watch — long-poll по `changes`.
- **v2:** etcd за тем же API, если понадобится HA/масштаб.
- Контракт API не зависит от store: замена — внутреннее дело.
- Бэкап: снапшот + WAL, RPO ≤ 5 мин; RTO ≤ 15 мин; проверка restore — в приёмке.

---

## 6. Кто становится контроллером

| Компонент | Было | Стало |
|---|---|---|
| massaru | CLI + файлы | контроллер `Epishu`: создаёт каталоги, запускает/останавливает процесс, пишет status |
| sessions | код + очередь в памяти | контроллер `Session`: один active через CAS/lease, очередь в status |
| RBAC | проверки в коде | `Grant` + admission; admin_epishu = scope |
| llm-gateway | конфиг в коде/env | клиент `Model`/`Quota`; ключи — в secrets, не в spec |
| metrics | exporter | читает status/audit, отдаёт в VM |
| eval | внешние скрипты | `EvalSuite/Task` (позже), evidence из audit |

`nqctl` — CLI (kubectl-подобный): `apply -f epishu.yaml`, `get`, `describe`, `logs`, `exec`. `massaru` остаётся именем контроллера, не пользовательским интерфейсом.

---

## 7. Что меняется в v1-плане

**Было:** massaru skeleton → gateway → 3 Awilu.
**Стало:**

1. **P0: apiserver-lite** — store + ресурсы Zi/Su/Epishu/Session/Grant + CRUD + watch + admission(RBAC/read-only) + audit + `nqctl`.
2. **P1: massaru-контроллер** — reconcile Epishu → каталоги/процесс; digest-проверка; bind_model.
3. **P2: session-контроллер** — одна активная, очередь, lease/epoch.
4. **P3: llm-gateway** — read-only, lease/квоты из `Quota`, usage ledger.
5. **P4: 3 Awilu** — как Epishu-объекты; прогон совета.
6. **P5: метрики/UI** — status + audit.

Slice 2 Дедала (layout/digest/bind_model) не отменяется, но переоформляется: логика — в контроллер, источник — `Epishu`.

---

## 8. Что НЕ копируем из k8s

- Не запускаем k8s/k3s и не тащим CRD-codegen.
- Не кладём в store данные (атомы памяти, промпты, метрики).
- Не делаем webhook-адмиссию в v1 — только встроенные проверки.
- Не делаем multi-apiserver/HA в v1: один узел + бэкап + документированный RTO.
- Не вводим namespaces как k8s: `scope` = epishu-id/tenant в Grant и Quota.

---

## 9. Риски и анти-паттерны

| Риск | Мера |
|---|---|
| Оверинжиниринг для 3 агентов | минимальный набор ресурсов; один бинарник; SQLite |
| apiserver — SPOF | running epishu живут без него; lease истекает → новые действия запрещены; бэкап/WAL |
| «Всё через API» убивает latency | data plane отдельно; watch-кэш у контроллеров |
| Дрейф файлов и API | файлы только материализация; drift → reconcile, не ручная правка |
| Сложность watch/revision | одна реализация, тесты на разрыв/rel ист; не плодить самодельные очереди |
| Секреты в spec | secrets отдельно; admission запрещает ключи в объектах |

---

## 10. Открытые вопросы (решу сам, если не поступит иначе)

1. Store: SQLite WAL (проще) vs Postgres (знакомее) — **SQLite в v1**, Postgres при HA.
2. Язык apiserver: Python (быстро) vs Go (жёстче) — **Python в v1**, контракт позволяет переписать.
3. `Effect` в v1: только схема + admission-deny — **да**.
4. UI: `nqctl` + ocmanager — **да**, отдельного портала не делаем.
