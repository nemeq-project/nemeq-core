# Перечень тестов Nemeq v0.4

**Статус:** draft · **Дата:** 2026-11-23 · **Автор:** Верста
**Связь:** `SDD_nemeq_v0.4.md` §13. Каждый тест — с мутантом (проверка, которая не может покраснеть, не проверяет).

## 1. Таблица

| ID | Тест | Тип | R | Шаги / ожидание | Мутант |
|---|---|---|---|---|---|
| T-01 | Зоны и режимы | unit | R-01 | 4 пути существуют; `state`/`data` под `/var/nemeq`; `/proc/nemeq` — bind-mount ro; запись → EACCES | убрать ro/подменить путь |
| T-02 | Digest mismatch | unit | R-02 | подменить файл zi после install → start отклонён; код ошибки | не проверять digest |
| T-03 | Overlay union | integration | R-03 | `view/` содержит zi+su; запись в zi/su → EROFS; upper в /run | писать в lower |
| T-04 | Zombie su | integration | R-04 | su без zi → Subur, без zikru-zi; zi без тела → inactive, `incarnations_count=0` | запускать как Awilu |
| T-05 | Epoch sinks | integration | R-05 | bump epoch; старый epoch → gateway/egress/WAL/settle/file отвергнуты | проверять только cgroup |
| T-06 | Sleep/wake | integration | R-06 | sleep: commit, cgroup пуст; wake: qatum/lease/revoked перепроверены | не перепроверять qatum |
| T-07 | Ug/Zinuntu | integration | R-07 | park_body сохраняет тело; destroy_anima → revoked, все инстансы zi остановлены | не проверять revoked |
| T-08 | WAL replay | integration | R-08 | kill после WAL без commit → `committed:false`; restart → replay без дублей | replay без commit-записи |
| T-09 | Claims/settle | unit | R-09 | повтор `effect_id` → известный статус, вторая запись не создаётся | писать второй раз |
| T-10 | Traces | unit | R-10 | каждый tool-вызов → step; ротация на 10 МБ сохраняет границу | не ротировать |
| T-11 | PII boundary | acceptance | R-11 | canary-ПД в prompt → только zikru-su; traces/WAL/usage redacted | пропустить ПД |
| T-12 | No keys in Awilu | acceptance | R-12 | `/proc/<pid>/{environ,maps,fd,cmdline}` → 0 ключей; canary | ключ в env |
| T-13 | Reservation | unit | R-13 | 2 параллельных запроса на остаток → один отказ; overrun флаг | без CAS |
| T-14 | Token fields | unit | R-14 | cached/uncached/output/reasoning; отсутствие → null, не 0 | zero-fill |
| T-15 | All calls | integration | R-15 | Subur+Awilu → ledger с epishu_id/kind | только Awilu |
| T-16 | Quotas | unit | R-16 | installation+epishu; без epishu → global до нуля | игнорировать global |
| T-17 | /proc | acceptance | R-17 | qatum/agents/sessions/context/cost/kalam; запись → EACCES (путь /run/nemeq/proc) | разрешить запись |
| T-18 | Massaru idempotency | unit | R-18 | повтор `request_id` → тот же результат; `install` по digest | без dedupe |
| T-19 | 3 Awilu | acceptance | R-19 | Октавия/Лира/Ада running на c-nemeq; sleep/wake | падать на второй |
| T-20 | Metrics | acceptance | R-20 | VM получает live-серии; дашборды не demo | demo-режим |
| T-21 | No secrets in logs | acceptance | R-21 | canary-ключ в stderr/traces/usage → 0 | логировать ключ |
| T-22 | Egress deny | acceptance | R-22 | AF_INET/AF_INET6/DNS/Unix: только allowlist; IPv6-обход закрыт | разрешить всё |
| T-23 | Crash между bump и kill | chaos | R-05 | kill после bump до kill → sink отвергает старый epoch | принимать старый |
| T-24 | Revoked при wake | integration | R-06/R-24 | revoke во сне → wake отказ | игнорировать revoked |
| T-25 | revoked_animas | unit | R-07/R-24 | install/run/wake/import проверяют файл; формат строк | не читать файл |
| T-26 | Torn WAL/checksum | chaos | R-08 | обрезанная строка/битый checksum → replay стоп, код 3 | пропустить строку |
| T-27 | Trace rotation | unit | R-10 | 10 МБ → новый файл; записи на границе целы | потерять границу |
| T-28 | Canary PII in streams | acceptance | R-11 | canary в claims/settlement/UL/OB → redaction/отказ | пропустить |
| T-29 | Reserve crash | chaos | R-13 | kill между reserve и request → lease истекает, reconcile | вечный резерв |
| T-30 | Secret canary in proxy | acceptance | R-21 | ключ в ответе proxy → не попадает в traces/логи | сохранить ответ |
| T-31 | IPv6/DNS escape | security | R-22 | прямой IPv6/DNS-туннель/Unix → deny | разрешить DNS |
| T-32 | Isolation escape | security | R-23 | symlink/`..`/hardlink из `view/` → отказ; чтение чужого `/var/nemeq/epishu/<id>` → EACCES | следовать symlink |
| T-34 | WAL day boundary | chaos | R-08 | torn tail в последнем файле vs mid-file в старом: обрезка только последнего; старый битый → код 3; смена суток без потери commit | обрезать оба |
| T-33 | Reserved features | unit | R-26 | `migrating`/продажа → отказ в v1 | принять команду |

## 2. Критерии приёмки пилота

- T-01…T-34 зелёные; мутанты — 34/34.
- T-19/T-20 на реальном `c-nemeq` (.46) и VM (.30).
- T-11/T-21/T-28/T-30 — canary-набор (ПД/ключи) без утечек.
- T-23/T-26/T-29 — chaos-прогон с рестартом.

## 3. Приборы

- `pytest` (unit/integration), `mutation_run.py` (мутанты), chaos-скрипт (kill -9 на шагах), canary-генератор, `curl` к VM/Grafana.
