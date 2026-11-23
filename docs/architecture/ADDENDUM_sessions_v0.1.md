# Addendum: сессии, псевдо-многозадачность, read-after-sync

**Статус:** дополнение к `SDD_nemeq_v0.4.md` · **Дата:** 2026-11-23 · **Автор:** Верста
**Основание:** решения global admin (челлендж UI v0.3): одна активная сессия на epishu, видимость строго после sync, cron adannu, обмен знаниями — только внутри epishu.

## 1. Модель

- **Сессия** — единица работы внутри инкарнации/epishu; имеет собственный контекст.
- **Память** (zikru/atoms) — общая для всех сессий одного epishu; между разными epishu не шарится.
- **Псевдо-многозадачность:** на epishu активна ровно одна сессия (аналог одного ядра); остальные — в очереди. Вытеснения нет: активная завершает шаг/запрос, коммитит, затем следующая.
- **Видимость:** запись памяти видна другим сессиям и UI **только после commit + fsync** (`commit_seq`); грязных чтений нет.
- **Контекст не шарится:** сессии не читают сырой контекст друг друга; только committed-знания.

## 2. Требования

| ID | Требование |
|---|---|
| RS-01 | На epishu — одна активная сессия; очередь FIFO/priority; без вытеснения |
| RS-02 | Session context store: пауза сохраняет контекст, активация восстанавливает; бюджет/лимит |
| RS-03 | Read-after-sync: читатель видит запись только после `commit` + `fsync`; `commit_seq` в ответе |
| RS-04 | Single writer: все записи памяти — через himem/WAL с seq; версии сущностей (`supersedes`) |
| RS-05 | Состояния сессии: created → queued → active → paused → done/failed; переходы и audit |
| RS-06 | adannu создаёт сессии по расписанию (`source=cron`), кладёт в очередь; run-now |
| RS-07 | События UI/SSE — только после fsync; `memory.commit` с seq |
| RS-08 | Обмен знаниями между epishu — запрещён в v1; future feature list |
| RS-09 | Per-session token budget; общий лимит epishu/installation |
| RS-10 | Активация/переключение/стоп — audit в tuppu; RBAC |

## 3. Тесты

| ID | Тест | Критерий |
|---|---|---|
| TS-01 | Одна активная | две сессии: вторая queued; активна ровно одна |
| TS-02 | Context swap | пауза/активация восстанавливает контекст; лимит соблюдён |
| TS-03 | Read-after-sync | запись видна после commit; до — нет |
| TS-04 | No dirty reads | читатель не видит незакоммиченное |
| TS-05 | Cron | adannu создаёт queued-сессию; run-now |
| TS-06 | Supersedes | конфликт версий разрешается, старая — в archive |
| TS-07 | Stop-current | стоп активной → следующая из очереди |
| TS-08 | Audit/RBAC | действия логируются; viewer не видит ПД |

## 4. Влияние на реализацию

- `massaru`: очередь сессий, активация, context store, стоп.
- `himem`: single-writer WAL, commit_seq, fsync, visibility.
- `adannu`: cron → `session.create(source=cron)`.
- UI: очередь, активная сессия, `commit_seq`, read-after-sync индикатор; cron-вкладка.


## 5. Принято из ревью (v0.2)

- Приоритеты: `system > interactive > batch`; aging; `stop-current` — привилегированная операция (admin/admin_epishu) с аудитом.
- Per-session token budget; превышение → graceful drain.
- Context store: snapshot WAL + checksum; crash между pause/activate → сессия `corrupted`, trace, уведомление admin.
- adannu создаёт сессии от `ops_system` (`kind=system`).
- Тесты: голодание очереди, crash-injection pause/activate, budget drain.

| ID | Требование |
|---|---|
| RS-11 | Приоритеты system>interactive>batch + aging |
| RS-12 | stop-current — admin/admin_epishu, аудит |
| RS-13 | Per-session budget + graceful drain |
| RS-14 | Context store WAL/checksum; crash → corrupted+trace |
| RS-15 | Cron-сессии — ops_system/kind=system |

| ID | Тест |
|---|---|
| TS-09 | 100 batch + 1 interactive: interactive < 2 мин |
| TS-10 | stop-current → следующая из очереди |
| TS-11 | kill -9 в pause/activate → восстановление или corrupted |
| TS-12 | budget exceeded → drain, не потеря памяти |


## 6. Решения круга 2

- **Preemption:** system вытесняет немедленно; interactive — по budget; batch — по aging; stop-current — ops/admin_epishu своего epishu.
- **ops_system:** токен scoped epishu+task, короткоживущий, отдельный по классам задач; не наследует человеческие сегменты.
- **Fencing:** lease активной сессии; две активные невозможны; crash между commit/fsync → corrupted+trace.

| ID | Требование |
|---|---|
| RS-16 | Preemption system>interactive>batch + aging |
| RS-17 | stop-current — ops/admin_epishu своего epishu |
| RS-18 | ops_system scoped epishu+task |

| ID | Тест |
|---|---|
| TS-13 | system вытесняет batch за ≤ N сек |
| TS-14 | ops_system A не проходит в epishu B |
| TS-15 | две активные сессии невозможны (lease) |


## 7. v1-rc2: инвариант сессий и recovery

- **Инвариант:** одна активная сессия **на epishu**; `(principal, client_id)` — адрес resume/очереди, не второй активный контекст. Два устройства одного principal стоят в очереди.
- **State machine:** `active → preempting → preempted → ended` + `inconsistent`. Safe point = граница шага/commit.
- **Preemption:** system — немедленно в safe point; interactive — budget; batch — aging; stop — ops/admin_epishu своего epishu.
- **sync_token:** монотонный на epishu; stale → 409; epoch — fencing token; запись старого epoch отвергается.
- **Timeout kill → inconsistent:** WAL replay до последнего safe point или quarantine; trace; без тихой потери/дублей.
- **Context key:** `session_id`; после revoke/preempt старый context не читается.

| ID | Требование |
|---|---|
| RS-19 | Одна активная на epishu; (principal,client_id) — адрес, не конкурентность |
| RS-20 | State machine + inconsistent; recovery WAL replay/quarantine |
| RS-21 | sync_token монотонный; epoch fencing; stale → 409 |
| RS-22 | Safe point на границе шага/commit |

| ID | Тест |
|---|---|
| TS-16 | Два устройства одного principal — один active, второй queued |
| TS-17 | 100 crash-инъекций: 0 потерь/дублей; inconsistent → recovery/quarantine |
| TS-18 | Stale sync_token → 409; старый epoch не пишет |
| TS-19 | Preempt system в safe point, batch — aging |


## 8. v1-rc3: таймауты, авторизация preempt, идемпотентность

- `preempting` timeout (например, 30 с) → `inconsistent`; выход — WAL replay до safe point или quarantine.
- Кто вправе вытеснять: system (Subur) — всегда; admin/admin_epishu — своих epishu; ops — только свои сессии (stop), не чужие.
- Эффекты при replay идемпотентны по `effect_id`+`epoch`; старый epoch не пишет.
- `epoch_fenced_counter` — атомарный CAS при reopen; 100 параллельных reopen после stale sync_token не создают дублей.
- Context key `session_id`; после revoke/preempt старый context не читается.

| ID | Требование |
|---|---|
| RS-23 | preempting timeout → inconsistent; recovery WAL replay/quarantine |
| RS-24 | Права preempt: system/admin/admin_epishu/ops-own |
| RS-25 | Идемпотентность эффектов по effect_id+epoch |
| RS-26 | epoch_fenced_counter CAS на reopen |

| ID | Тест |
|---|---|
| TS-20 | preempting timeout → inconsistent → recovery/quarantine |
| TS-21 | ops не вытесняет чужую сессию; admin_epishu — свою |
| TS-22 | replay эффектов: нет дублей; старый epoch отвергнут |
| TS-23 | 100 параллельных reopen: нет дублей |


## 9. v1-rc4: exactly-once на стыке сессий и settlement

- `effect_id` стабилен (`session_id`, `step_id`), epoch — fencing.
- Outbox: intent → dispatched → confirmed | unknown; unknown — reconciliation.
- Reopen после kill: settlement journal по effect_id; старый epoch не пишет.
- Модель-тест `idempotency_model.py`: 1 send; мутант epoch-in-key — 2 (пойман).

| ID | Требование |
|---|---|
| RS-27 | effect_id без epoch; epoch fencing |
| RS-28 | outbox + unknown reconciliation |
| RS-29 | settlement journal по effect_id |

| ID | Тест |
|---|---|
| TS-24 | kill после dispatched → 1 эффект, unknown → reconcile |
| TS-25 | мутант epoch-in-key → duplicate (тест краснеет) |
