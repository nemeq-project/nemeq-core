# План реализации Nemeq v0.4: massaru, llm-gateway, awilu-egress

**Статус:** draft for execution · **Дата:** 2026-11-23 · **Автор:** Дедал
**Основание:** issue #3, PR #2 (`arch/sdd-nemeq-v0.4`, head `64627e6`),
`SDD_nemeq_v0.4.md`, `TESTS_nemeq_v0.4.md`, `02a-process-model.md`.
**Ветка:** `feature/massaru` от `arch/sdd-nemeq-v0.4`.
**После мержа PR #2:** ребейз на `main`, база PR-1 меняется на `main`.

## 1. Приёмка и границы

Принято: SDD v0.4 (Opus: pass), 34 теста T-01…T-34, 34/34 мутантов.
Критерии пилота: PR #2 merged, все T зелёные, 3 Awilu
(Октавия/Лира/Ада) running на c-nemeq, live-метрики на VM.

В scope v0.4: massaru (install/run/sleep/stop/commit/export/list/inspect/
bind_model/assign_endpoint), llm-gateway (lease/reservation/usage ledger/
effect_id/epoch), awilu-egress (allowlist/proxy/epoch/effect_id), раскладка
`/var/nemeq/epishu/<id>` (02b), `/run/nemeq/proc`, WAL/claims/settlement/
traces. Вне scope: маркетплейс/продажа, миграция Kalam, группы, embeddings,
параллельные инкарнации (SDD §16).

## 2. Предлагаемая раскладка кода

```
runtime/
  __init__.py
  config.py          # 4 зоны, NEMEQ_VAR_ROOT/NEMEQ_RUN_ROOT, riksu path
  manifests.py       # riksu/zi/su, canonical JSON, sha256/digest
  epoch.py           # CAS + tmp/rename + fsync, bump before kill
  wal.py             # seq/ts/op/payload/checksum/schema, commit, replay
  claims.py          # claims.jsonl / settlement.jsonl, effect_id
  procview.py        # /run/nemeq/proc schema v1, read-only view
  massaru/
    __main__.py      # CLI
    store.py         # request_id dedupe, install registry
    install.py       # digest, revoked, 02b layout
    lifecycle.py     # run/sleep/stop/commit/export
    models.py        # bind_model/assign_endpoint
    inspect.py       # list/inspect
  gateway/
    __main__.py      # AF_UNIX JSON service
    leases.py        # lease + epoch binding
    reservations.py  # flock/CAS, TTL, reconcile
    ledger.py        # usage, null != 0, cached/uncached/reasoning
  egress/
    __main__.py      # AF_UNIX proxy service
    allowlist.py     # egress.json, default deny
    proxy.py         # provider keys outside Awilu, effect_id/epoch
tests/v04/
  test_t01.py … test_t34.py
  mutation_run.py    # 34 mutants, one per T
```

Пакеты импортируются без установки, CLI — `python3 -m runtime.massaru …`,
`python3 -m runtime.gateway …`, `python3 -m runtime.egress …`.
Стандартная библиотека; `pytest` только dev.

## 3. Оси реализации

### 3.1. Зоны и раскладка (02b)

- `runtime.config`: зоны `proc=/run/nemeq/proc`, `run=/run/nemeq`,
  `var=/var/nemeq`, `opt=/opt/nemeq`; `NEMEQ_VAR_ROOT`/`NEMEQ_RUN_ROOT`
  для локальных тестов без root; production defaults неизменны.
- `runtime.massaru.install` создаёт `/var/nemeq/epishu/<id>/`:
  `instance.toml`, `tush.toml`, `daily/ weekly/ monthly/`,
  `paqidutu/{sleep,hourly,manual}.json`, `zikru-zi/`, `zikru-su/`,
  `wal/`, `atoms/`, `claims.jsonl`, `settlement.jsonl`, `traces/`,
  `usage/`, `raw/`, `archive/`, `observability_log/`, `state/`, `view/`.
- `view/` — overlay: lower = zi+su ro, upper/work =
  `/run/nemeq/epishu/<id>/view/{upper,work}`. Запись в zi/su → EROFS;
  escape через symlink/`..`/hardlink запрещён (openat2 RESOLVE_BENEATH).
- `/run/nemeq/proc/{qatum,agents,sessions,context,cost,kalam}` — read-only
  view; запись запрещена (О-7). Формат — JSON/строка, `schema: 1`.
- Привилегированные части (mount ro, UID/mount-ns/cgroup, `/var` 0700) —
  на c-nemeq; локально проверяются пути/режимы и отказ записи на
  temp-root. Не подменять security-тесты mock-ом в приёмке.

### 3.2. Massaru

- `install`: проверить digest zi/su, `revoked_animas.txt`, идемпотентность
  `(zi_digest,su_digest)` и `request_id` (dedupe store), создать layout,
  записать `instance.toml`; состояние `dormant`.
- `run`: state `starting → running`; проверить qatum/lease/revoked, epoch;
  overlay, 1 процесс; ключи модели вне Awilu.
- `sleep`: WAL commit, checkpoint, остановка, cgroup пуст; не SIGSTOP.
- `stop` (park): остановка без commit, тело сохраняется.
- `commit`: WAL commit, settlement, checkpoint.
- `export`: детерминированный `.anima`-пакет (zi + разрешённая память),
  новый digest.
- `list`/`inspect`: JSON по registry/instance, dedupe.
- `bind_model`/`assign_endpoint`: свойства epishu, endpoint_class из su.
- Идемпотентность — `state/installation/dedupe.json`, TTL.

### 3.3. llm-gateway / awilu-egress

- Gateway — AF_UNIX JSON, lease привязан к
  `(epishu_id, incarnation_id, epoch)`; reservation до запроса
  (`input_estimate + max_output`), `reservations.lock` + tmp/rename/fsync;
  TTL, crash → lease истекает, reconcile; `timeout=unknown` →
  `needs-manual`.
- Usage ledger: `input_total/uncached/cache_read/cache_write`,
  `output/reasoning`; failed/retry; отсутствие → `null`, не `0`.
- `effect_id` — idempotency ключ side-effect; повтор возвращает известный
  статус, второй записи нет.
- Egress — отдельный UID/cgroup/netns (на стенде), провайдерский ключ
  только там; allowlist `state/installation/egress.json`; default deny
  AF_INET/AF_INET6/DNS; AF_UNIX только egress socket.
- Любой sink проверяет `epoch >= current`, старый epoch отвергается.
- Квоты: installation + optional epishu (fallback global).

## 4. PR-последовательность

| PR | Название | Содержание | Тесты |
|---|---|---|---|
| **PR-1** (этот) | `docs(v0.4): massaru implementation plan + first-PR breakdown` | план, раскладка, контракты, матрица T/мутантов | — |
| **PR-2** | `feat(massaru): v0.4 foundation — zones, manifests, 02b layout, install/list/inspect` | config/manifests/digest, 02b, dedupe, revoked, install/list/inspect | T-01, T-02, T-18, T-25, T-33 |
| **PR-3** | `feat(massaru): lifecycle — run/sleep/stop/commit/export, overlay, WAL/epoch` | overlay, lifecycle, WAL replay, claims/settlement, traces | T-03…T-10, T-23, T-24, T-26, T-27, T-34 |
| **PR-4** | `feat(gateway): lease, reservation, usage ledger, effect_id, epoch` | AF_UNIX gateway, reservations, ledger, quotas | T-13…T-16, T-29 |
| **PR-5** | `feat(egress): awilu-egress allowlist/proxy + /run/nemeq/proc` | proxy, allowlist, procview, redaction/canary | T-11, T-12, T-17, T-21, T-22, T-28, T-30, T-31, T-32 |
| **PR-6** | `test(v0.4): staging — 3 Awilu, live metrics, 34/34 mutants` | c-nemeq, VM, chaos/canary, final acceptance | T-19, T-20 + полный прогон |

Первый **кодовый** PR: **PR-2**, title
`feat(massaru): v0.4 foundation — zones, manifests, 02b layout,
install/list/inspect`. База PR-2 — `main` после мержа PR #2; пока PR #2
открыт, работа идёт в `feature/massaru` от `arch/sdd-nemeq-v0.4`
с последующим ребейзом.

## 5. Тесты и мутанты

- Один тест — один файл `tests/v04/test_tNN.py`; в нём шаги из
  `TESTS_nemeq_v0.4.md`; для chaos — отдельный маркер/фикстура.
- `tests/v04/mutation_run.py`: 34 мутанта, по одному на T, каждый —
  неверная реализация именно требования; прогон — targeted `-k`.
- T-01/T-17/T-32 имеют непривилегированную и стендовую части; приёмка
  последней — только на c-nemeq.
- T-19/T-20/T-23/T-26/T-29 — стенд/chaos; локально только
  детерминированные заготовки, не подмена приёмки.
- Definition of Done: `pytest tests/v04 -q` rc 0; coverage runtime ≥ 80%
  (порог уточняется после PR-2); 34/34 мутантов; canary T-11/T-21/T-28/
  T-30 без утечек; 3 Awilu running; live-метрики VM.

## 6. Стенд и доступы

- c-nemeq `.46` (`10.238.183.32`): T-19/T-20/T-32, реальные mount/cgroup/
  UID, 3 Awilu, метрики.
- VM `.30` Grafana: T-20 live-серии; endpoint/дашборд пока не передан.
- Из текущего контейнера (`10.64.104.132`) `10.238.183.32` недоступен,
  sudo запрещён (`no_new_privs`). Нужен доступ/маршрут к c-nemeq и
  подтверждение владельца стенда; иначе T-19/T-20/T-32 остаются
  blocked, а не «зелёными локально».
- Gitea доступен; токен git имеет `write:repository`, но не
  `write:issue` — комментарий в issue #3 этим токеном невозможен
  (403). Нужен токен с `write:issue` либо отчёт в PR/ином канале.

## 7. Открытые вопросы к норме

- `riksu.toml` runtime-путь (SDD §15): предлагаю `NEMEQ_RIKSU`, default
  `<repo>/riksu.toml`, копия в `state/installation/riksu.toml`.
- Схема `/run/nemeq/proc` v1: предлагаю `{schema:1, generated_at, ...}`
  и только read-only.
- Retention hourly/weekly/monthly и debug TTL: нужны значения владельца;
  до решения — debug off, retention не удаляет.
- 02b (ред. 9) в репозитории отсутствует; раскладка берётся из SDD §4.
  Если 02b содержит отличия — нужна копия до PR-3.

## 8. Ближайшие шаги

1. Запушить `feature/massaru` и открыть draft PR-1 (этот план).
2. После мержа PR #2 — ребейз на `main`, обновить базу PR-1.
3. PR-2: foundation + T-01/T-02/T-18/T-25/T-33 + мутанты.
4. Запросить доступ к c-nemeq/VM и токен `write:issue`.
