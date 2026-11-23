# SDD Nemeq v0.4 — среда исполнения Awilu

**Статус:** draft for handoff (v0.1/v0.2: ревью; v0.4 закрывает текстовые остатки ревью v0.3) · **Дата:** 2026-11-23 · **Автор:** Верста
**Основание:** `02a`, `02b` (ред. 9), `nemeq_token_metrics_v0.1`, ревью Opus/Sonnet/GPT/DeepSeek.

## 1. Scope и термины

Nemeq — harness/«ОС для агентов»: Libbu (6 примитивов), Subur (service), Awilu (zi+su). В v1 — запуск/сон/смерть, память, права, метрики. Вне v1: маркетплейс, миграция Kalam, группы, embeddings.
zi/anima — душа-образ; su/body — тело-образ; epishu — агент; Tush — инкарнация; Kalam — инстанс Libbu; qatum — право; paqidutu — handoff; Ug/Zinuntu — смерть тела/анимы; zikru-zi/su — память души/тела.

## 2. Зоны (4)

| Зона | Путь | Режим |
|---|---|---|
| proc | `/run/nemeq/proc` (read-only view) | live, генерируется |
| run | `/run/nemeq` | volatile |
| var | `/var/nemeq` | mutable |
| opt | `/opt/nemeq` | immutable |

Путь `/proc/nemeq` в v1 **не используется**: procfs не позволяет создать mountpoint. Канонический путь — `/run/nemeq/proc`; `/proc/nemeq` — reserved (FUSE/модуль, вне v1).

## 3. Образы

zi: `manifest.json` (sha256+digest), `anima.toml`, `skills/`, `knowledge/`, `style/`, `zikru-zi/`.
su: `manifest.json`, `body.toml`, `tools/<tool>/manifest.toml` (`capability=<qatum>`), `endpoint_class`.
`anima_id = sha256(canonical_json({psychomatrix, prompts_final}))` — канонизация: ключи сортированы, UTF-8, без пробелов.
В v1: **один zi ↔ не более одного активного epishu** (параллельные инкарнации запрещены) — Zinuntu однозначен.

## 4. Инстанс

`/var/nemeq/epishu/<epishu-id>/`: `instance.toml` (zi/su refs+digests, `incarnation_id`, `generation`, `kind`, `state`, `lease_ref`), `tush.toml`, `daily/ weekly/ monthly/`, `paqidutu/{sleep,hourly,manual}.json`, `zikru-zi/`, `zikru-su/`, `wal/`, `atoms/`, `claims.jsonl`, `settlement.jsonl`, `traces/`, `usage/`, `raw/`, `archive/`, `observability_log/`, `state/` (memory-index/fts/hot/usage/day-status/checkpoint), `view/` (overlay, upper/work — `/run/nemeq/overlay/<id>/{upper,work}`).
Su без Zi = Subur-«зомби» (без zikru-zi, минимальный qatum); zi без тела — артефакт.

## 5. Идентичность

`epishu_id` — UUID инстанса; `incarnation_id` — UUID пары; `generation` — счётчик перезапусков; `incarnation_epoch` — монотонный, хранится в `/var/nemeq/state/installation/epochs/<epishu-id>.json` (CAS + fsync). Связь: epishu 1—0..N incarnation 1—0..N generation.

## 6. Жизненный цикл

Состояния: `dormant → starting → running → sleeping → dead`, плюс `failed`, `crashed`; `migrating` — reserved (не в v1).
Сон = commit + остановка (не SIGSTOP), cgroup пуст. Park (Ug) = остановка без commit, тело пакуется. Wake перепроверяет qatum/lease/revoked.
Fencing: durable bump epoch → kill cgroup (оптимизация) → wait empty → смена владельца → новая generation.

## 7. Failure & Security Model (нормативно)

1. **Epoch у каждого sink.** Любой side-effect несёт `(epishu_id, epoch, effect_id)`; приёмник отклоняет при `epoch < current`: `llm-gateway`, `awilu-egress`, WAL-writer, settlement, file-writers.
2. **CAS-хранилище epoch** — `state/installation/epochs/<epishu-id>.json`, запись `tmp+rename+fsync`; bump до kill.
3. **WAL** — запись `{seq,ts,op,payload,checksum,schema}`; commit — отдельная запись `{seq,op:"commit"}`; хвост после последнего commit обрезается (torn tail); повреждение в середине/checksum mismatch → стоп replay, код 3; checkpoint = последний commit.
4. **Crash-матрица:** reserve→crash (lease истекает), request→crash (timeout=unknown), settle→crash (идемпотентный повтор), bump→crash до kill (sink отвергнет старый epoch), kill→crash (recovery по epoch/generation).
5. **Изоляция:** каждый Awilu — свой UID, mount-ns, cgroup, `/var/nemeq/epishu/<id>` 0700; zi/su монтируются ro; запрещены symlink/hardlink-escape (openat2 RESOLVE_BENEATH).

## 8. Память и ПД

- WAL `/var/nemeq/epishu/<id>/wal/<yyyymmdd>.jsonl`; atoms `/var/nemeq/epishu/<id>/atoms/`; index `/var/nemeq/epishu/<id>/state/{memory-index.json,fts.jsonl,hot.json}`; claims/settlement `/var/nemeq/epishu/<id>/*.jsonl` (epoch в записи); traces `/var/nemeq/epishu/<id>/traces/<task_id>/steps.jsonl`; raw off/debug; UL `/var/nemeq/data/upstream_log/<YYYY-MM>/`; OB `/var/nemeq/epishu/<id>/observability_log/<YYYY-MM>/`; archive per epishu.
- L0/L1/L2 генерируются из zikru; **ПД только в `zikru-su`** (контур-специфичное, включая IP).

**Классификация потоков:**

| Поток | ПД | Политика |
|---|---|---|
| zikru-su | да | 0700, только тело; не экспортируется с zi |
| traces args/results | возможны | redaction по каталогу правил; хэш+усечение |
| WAL/atoms/claims | возможны | шифрование at-rest или redaction; доступ per-instance |
| settlement | хэш | fingerprint, без содержимого |
| usage | нет (метрики) | без payload |
| UL/OB | возможны | redaction, retention |
| raw | да | off по умолчанию; debug по qatum, TTL, автопурга |

## 9. LLM, модель, egress

Модель — свойство epishu: su объявляет endpoint_class; `massaru.bind_model`/`assign_endpoint`. Ключи — только в `llm-gateway`/`awilu-egress` (UID/cgroup отдельно). Lease: `max_input_tokens`, `max_output_tokens`, `max_requests`, `concurrency`, `max_cost_rub`, `cache{}`, `models[]`.
Egress allowlist — `state/installation/egress.json` (host:port/proto); default deny для AF_INET/AF_INET6, разрешён только proxy; AF_UNIX — только к egress-сокету. Инструменты: `su/tools/<tool>/manifest.toml` (поля: `name`, `capability`, `endpoint`, `args_schema`, `egress`; `args_schema` — JSON Schema); capability проверяется в dispatch через qatum.

## 10. Токены и квоты

Считаются все вызовы Awilu и Subur: `input_total/uncached/cache_read/cache_write`, `output/reasoning`; failed/retry; `null ≠ 0`.
Ledger: `/var/nemeq/epishu/<id>/usage/<yyyymmdd>.jsonl`; live `/run/nemeq/proc/cost/<epishu-id>/`; агрегаты `/var/nemeq/epishu/<id>/state/usage.json`, `state/installation/{usage,reservations,pricing}.json`.
Reservation — отдельный `reservations.lock` (flock), version, `tmp+rename` + `fsync` файла и каталога; `lease_id` + TTL; crash → lease истекает, reconcile; `timeout=unknown` → `needs-manual`. Квоты: installation + optional epishu (fallback global).

## 11. `/run/nemeq/proc`

`qatum/`, `agents/`, `sessions/`, `context/`, `cost/`, `kalam`; read-only; запись запрещена (О-7). Формат — JSON/строка, версионируется в схеме.

## 12. Massaru API

`install`, `remove`, `run`, `sleep`, `stop` (park), `commit`, `export`, `list`, `inspect`, `bind_model`, `assign_endpoint`. Идемпотентность: `request_id` (TTL-store `state/installation/dedupe.json`); `install` — по `(zi_digest,su_digest)`.
`revoked_animas.txt` — `state/installation/revoked_animas.txt`, владелец massaru, строки `anima_id<TAB>ts<TAB>reason`; проверка при `install/run/wake/import`.

## 13. Требования

| ID | Требование | Тест |
|---|---|---|
| R-01 | 4 зоны; state/data — подкаталоги /var; proc = `/run/nemeq/proc` | T-01 |
| R-02 | zi/su ro; digest при install/start; TOCTOU-защита | T-02 |
| R-03 | Инстанс = zi∪su overlay; upper/work в /run | T-03 |
| R-04 | Su без Zi = Subur; zi без тела inactive | T-04 |
| R-05 | Epoch у всех sinks; CAS+fsync; crash-матрица | T-05, T-23 |
| R-06 | Сон = commit+пустой cgroup; wake перепроверяет qatum/lease/revoked | T-06, T-24 |
| R-07 | Park (Ug) / destroy (Zinuntu) + revoked-проверка | T-07, T-25 |
| R-08 | WAL с commit-записью, torn/checksum, replay | T-08, T-26 |
| R-09 | claims/settlement с epoch | T-09 |
| R-10 | traces для tool-вызовов; ротация 10 МБ | T-10, T-27 |
| R-11 | ПД только в zikru-su; классификация потоков | T-11, T-28 |
| R-12 | Модель через massaru; ключи вне Awilu | T-12 |
| R-13 | Lease/reservation CAS+TTL; overrun | T-13, T-29 |
| R-14 | Токены cached/uncached/reasoning; null≠0 | T-14 |
| R-15 | Все вызовы Awilu+Subur в ledger | T-15 |
| R-16 | Квоты installation+epishu | T-16 |
| R-17 | /run/nemeq/proc read-only, формат | T-17 |
| R-18 | Massaru идемпотентно (request_id/digest) | T-18 |
| R-19 | 3 Awilu на c-nemeq | T-19 |
| R-20 | Метрики live в VM | T-20 |
| R-21 | Нет секретов/ПД в логах и трассах (canary) | T-21, T-30 |
| R-22 | Egress deny: AF_INET6/DNS/Unix | T-22, T-31 |
| R-23 | Изоляция: UID/mount-ns/0700; escape-тесты | T-32 |
| R-24 | revoked при install/run/wake/import | T-25 |
| R-25 | Dedupe install/list | T-18 |
| R-26 | Продажа/migrating — reserved, не в v1 | T-33 |

## 14. Перечень тестов

Полный список с шагами — `TESTS_nemeq_v0.4.md` (в этом же каталоге).

## 15. Открытые вопросы

- `riksu.toml` рантайм-путь; формат `/run/nemeq/proc` (схема v1); retention `hourly/weekly/monthly`; debug TTL.

## 16. Не-цели v1

Маркетплейс/продажа, миграция Kalam, группы, embeddings, параллельные инкарнации.
