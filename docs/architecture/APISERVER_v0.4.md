# APISERVER v0.4 — централизованная управляющая плоскость Nemeq

**Дата:** 2026-11-24 · **Статус:** проект v0.4 (заменяет v0.3; store — текстовый журнал) · **Решение:** D-11 · **Владелец:** Верста (CEO)
**Принцип:** берём **идеи** Docker/Kubernetes, не реализацию. Ни k8s, ни etcd, ни CRD-машинерии.

---

## 1. Что берём из k8s (идеи)

| Идея | Как у нас |
|---|---|
| Declarative spec/status | объект описывает желаемое; контроллер пишет фактическое |
| Reconciliation | цикл «сравни → приведи», а не команды «сделай» |
| resourceVersion | монотонная ревизия; update/delete с предусловием |
| watch | подписка с ревизии, без поллинга |
| Admission | проверки до записи: authz, quota, policy |
| Finalizers | удаление через остановку, а не мгновенно |
| Namespaces → scope | `epishu-id`/tenant в Grant/Quota, без k8s-namespace |
| Audit | журнал изменений как первоклассный объект |
| GitOps | манифесты в Gitea + `nqctl apply` + drift detection |

**Не берём:** k8s/k3s, CRD-codegen, admission-webhooks, HA-кластер, etcd как зависимость.

---

## 2. Control plane ≠ data plane

| Плоскость | Что | Путь |
|---|---|---|
| Control | Zi, Su, Epishu, Session, Grant, Quota, Model | только apiserver |
| Data | zikru-атомы, эмбеддинги, промпты, LLM-ответы, метрики, логи | свой API + токен + read gate |

Истина — объект в API. Файлы (02b-layout, `instance.toml`) — **материализация** контроллера. Прямые записи «компонент → диск» и «компонент → компонент» запрещены.

**Endpoint:** `https://<host>:8448` (mTLS). Порт 8448, не 6443 (kube-apiserver) и не 8443 (LiteLLM). Меняется через `NEMEQ_API_PORT`.

---

## 3. Store: текстовый журнал (TEXTSTORE v0.2)

Источник истины — **append-only журнал JSONL**; состояние — материализация; MD/CSV — представления. Ноль зависимостей, audit by construction. Детали, формат, восстановление, замеры и тесты — `TEXTSTORE_v0.2.md`.

Коротко:
- одна строка = одна транзакция (`rev`, `prev`, `txn`, `hash`), глобальный hash-chain;
- один писатель (`flock`), group commit 2–10 мс, ответ после `fsync`;
- снапшоты каждые 1000 ревизий/5 мин, сегменты 64 МБ, старые не удаляем без бэкапа;
- `fsck` при старте: torn tail обрезается, битая середина — стоп и restore;
- замер: 20 ops/s при fsync-на-запись, **1 922 ops/s** при batch=100; 5 000 записей = 1,33 МБ.

## 4. API-семантика

1. `resourceVersion` на каждом объекте; update/delete с предусловием; 409 при конфликте.
2. `watch` c `fromRev`, `ADDED/MODIFIED/DELETED`, bookmarks, разрыв → relist.
3. `spec/status`; `status` — subresource, пишет только контроллер.
3a. `rev` vs `resourceVersion`: `rev` — глобальный int для watch/bookmarks; `resourceVersion` — строка `<rev>-<uid8>` для CAS. Устаревшая → 410. `generation`/`observedGeneration`; Lease для лидерства контроллеров.
4. Admission: validation → defaults → authz → quota → policy → persist. v1 policy: deny внешних эффектов и write-capability.
5. Finalizers/`deletionTimestamp`: удаление Epishu = остановка сессии → снятие объекта.
6. Audit: запись в журнал (кто, что, hashes, requestId) + экспорт.
7. Ошибки RFC 7807 + reason (`Conflict`, `Forbidden`, `Invalid`, `QuotaExceeded`).
8. Idempotency: create по имени, delete идемпотентен; data plane — `request_id`/`effect_id`.

---

## 5. Ресурсы v1alpha1

`/apis/nemeq.io/v1alpha1/...`

| Ресурс | spec | status |
|---|---|---|
| Zi | image, digest, manifest | phase, verified |
| Su | image, digest, capabilities | phase, verified |
| Epishu | ziRef, suRef, model, quotas, kind, readOnly | phase, node, sessionRef, conditions |
| Session | epishuRef, principal, clientId, priority, budget | phase, queuePos, syncToken, epoch |
| Grant | subject, role, scope, verbs | — |
| Quota | scope, tokens, cost, concurrency | used, limit |
| Model | provider, modelId, context, price | healthy, version |
| Effect | sinkClass, payloadHash, idempotencyKey | phase (только схема; v1 admission deny) |

---

## 6. Контроллеры

| Компонент | Роль |
|---|---|
| massaru | reconcile `Epishu` → каталоги/процесс, digest, bind_model, status |
| session | `Session`: один active через CAS/lease, очередь в status |
| rbac | `Grant` + admission; scope = epishu/tenant |
| llm-gateway | клиент `Model`/`Quota`; ключи в secrets, не в spec |
| metrics | status/audit → VictoriaMetrics |

---

## 7. Язык

- **v1: Python** (asyncio) — скорость разработки, текущее ядро на Python; при нашей нагрузке (3–20 epishu) хватает.
- **Переписывание — Rust, не Go.** Причины: memory safety без GC, предсказуемая latency, один статический бинарник, совпадает с планом «Libbu на Rust».
- **Почему не Go сейчас:** Go прагматичен для k8s-машинерии (client-go, apimachinery), но мы её не используем; Go даёт скорость разработки, но не даёт того, ради чего переписывать (Rust — безопасность и latency). Go допустим только как временный инструмент для утилит, не для ядра.
- **Критерий переписывания:** p99 > 50 мс на API при 50 rps, или GC-паузы > 10 мс, или HA-требование с репликацией. До этого Python.

---

## 8. UI

- **`nqctl` — CLI** (kubectl-подобный): `apply/get/describe/logs/exec`, GitOps-вход. Для операторов и агентов.
- **ocmanager (переработанный) — веб-UI**: каталог epishu, сессии/очередь, права, audit, метрики. Отдельный портал не делаем.
- API — единственный бэкенд для обоих; UI не пишет в обход.

---

## 9. Workflow детальной проработки и ревью

### Роли

| Роль | Кто | Что делает |
|---|---|---|
| Владелец | Верста | ADR, финальная подпись, приёмка |
| Архитектор API | Дедал | схема ресурсов, store-инварианты, прототип P0 |
| Оценщик/evidence | Нисаба | conformance-тесты, audit-проверка, приёмка замером |
| Red team | Эрра | threat model API: токены, watch-утечки, abuse, replay |
| Редактор | Гермес | консистентность терминов, читаемость, противоречия |
| Внешний ревьюер | Луч | независимое ревью спеки и тестов |
| Эксперты | Opus, Grok, Gemini | красная команда архитектуры (идеи, не реализация) |
| Куратор | Куратор | таксономия ресурсов, глоссарий, связь с SDD |

### Этапы и гейты

| Этап | Срок | Артефакт | Гейт |
|---|---|---|---|
| 0. ADR | сделан | `APISERVER_v0.2.md` | принят владельцем |
| 1. Deep design | 2 дня | `API_SCHEMA.md` (OpenAPI-фрагмент), `RESOURCE_CATALOG.md`, `INVARIANTS.md` | нет противоречий, все поля spec/status описаны |
| 2. Threat model | 1 день | `THREAT_MODEL.md` (Эрра + Луч) | нет необработанных угроз P0 |
| 3. Conformance plan | 1 день | `CONFORMANCE.md` (Нисаба) | тесты на resourceVersion/watch/admission/audit/restore + мутанты |
| 4. Внешний челлендж | 1 день | `REVIEWS/` (Opus, Grok, Gemini, Луч) | все замечания с предложением и критерием; стопперов нет |
| 5. P0 реализация | 3–5 дней | apiserver-lite + `nqctl` | conformance зелёный, audit проверяем |
| 6. Пилот massaru | 2 дня | контроллер Epishu | 1 epishu создан/остановлен через API, файлы — материализация |
| 7. Приёмка | 1 день | `ACCEPTANCE.md` | Нисаба воспроизвела, Верста подписала |

### Правила ревью

- Критикуя — предлагай: замечание → предложение → критерий.
- Число без источника — не число.
- «Идея из k8s» проверяется вопросом: это паттерн или зависимость? Зависимость — отклоняется.
- Спор решается владельцем; несогласный фиксируется в ADR.

---

## 10. Приёмка P0 (замером)

1. `nqctl apply -f` создаёт Zi/Su/Epishu; повтор — 409/идемпотентно.
2. Два параллельных update с одним `resourceVersion` → один 200, один 409.
3. Watch с `fromRev` отдаёт все изменения; разрыв → relist без потерь.
4. Убийство процесса на записи → после рестарта журнал цел, hash-chain сходится.
5. Restore из снапшота + сегментов → ревизия и состояние совпадают.
6. Попытка создать Epishu с внешним эффектом → 403 admission.
7. Каждая запись в audit: actor, requestId, hashes; подмена байта в сегменте ловится.
8. SLO (гистограммы): read p99 ≤ 50 мс; write p99 ≤ 150 мс при ≤50 rps, p95 ≤ 100 мс.

---

## 11. Риски

| Риск | Мера |
|---|---|
| Свой журнал = свой баг | тесты crash/partial write, hash-chain, restore-drill |
| Оверинжиниринг | минимум ресурсов, один бинарник, P0 без HA |
| apiserver — SPOF | running epishu живут; lease истекает; RTO ≤15 мин |
| Watch-утечки | scope в watch, authz на подписку, тест Эрры |
| «Идеи» превращаются в k8s | чек-лист «паттерн или зависимость» на каждом ревью |
