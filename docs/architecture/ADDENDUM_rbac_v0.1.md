# Addendum: RBAC, группы, сегментация zikru-su

**Статус:** дополнение к `SDD_nemeq_v0.4.md` · **Дата:** 2026-11-23 · **Автор:** Верста
**Основание:** решения global admin по ролям и PII: admin / admin_epishu / ops; группы epishu и пользователей; zikru-su по пользователю; zikru-zi — read-only для ops.

## 1. Роли

- **admin (global, root)** — развернувший инсталляцию Libbu; всё: все epishu, сессии, память, группы, гранты, lifecycle, аудит; **прямой доступ к файлам инсталляции** (root). Sealed защищает от admin_epishu, не от root.
- **admin_epishu** — владелец конкретного epishu (или группы epishu): видит всё по своим epishu, включая все сегменты `zikru-su` и все сессии, **кроме sealed** (там — только metadata); другие epishu не видит; управляет lifecycle/квотами.
- **ops (user)** — работает с epishu через **свои** сессии; видит только свои сессии и **свой сегмент** `zikru-su`; `zikru-zi` — read-only (общий для epishu); писать в `zikru-zi` не может.
- **viewer** — read-only: метрики, каталог/состояния epishu, аудит-события; без контента, ПД, контекста и lifecycle. Заменяет auditor.

## 2. Сегментация zikru-su

Запись: `epishu_id`, `owner_user_id`, `scope` (субъект/контур), `value`, `source`, `ts`, `access=private`.
- **Чтение:** ops — только `owner_user_id == self`; admin_epishu — все сегменты своего epishu, **кроме sealed** (metadata); admin — все (sealed — break-glass).
- **Запись:** epishu пишет от имени владельца сессии, проставляет `owner_user_id`; PII-скан; ops не пишет чужие сегменты.
- **Фильтрация — в сервисе памяти (himem/recall)**, не только в UI; UI не получает чужие записи.
- **Аудит:** каждое чтение ПД — запись в `tuppu` (кто, epishu, scope, зачем).

## 2a. Sealed (вариант B — opt-in)

- `sealed=true` для `sensitive_personal` (opt-in) и **всегда** для `secret`.
- `admin_epishu` видит metadata (размер, число записей, владелец, классификация, даты); content через API — `403`.
- **Global admin (root)** имеет прямой доступ к файлам инсталляции; sealed защищает от admin_epishu, **не от root**. Это строгая граница.
- Аудит доступа к ПД — tamper-evident/best-effort; root может изменить файлы, поэтому «immutable от root» не заявляем.
- Owner-ops всегда видит свой сегмент; массовая выгрузка запрещена.
- Sealed не попадает в обычный recall/поиск и в выгрузки; 2-of-N/quorum не вводим.
- **v1:** sealed = ACL/FS; at-rest шифрование не делаем (F-01 в feature list); root trusted — принятый риск.

## 3. zikru-zi

- **Read:** все пользователи/сессии данного epishu (общий, без ПД).
- **Write:** только epishu/himem — дистилляция из взаимодействия с ops; **строго авто**: PII-детект → блок; k-support < 3 → quarantine; sensitive → sealed; публикация без ручного approval; provenance; **ops не пишет**.
- **Cross-epishu:** запрещено в v1; future feature list.

## 4. Группы (везде)

- **Ресурсные группы:** epishu-группы и пользовательские группы.
- **Гранты:** `(principal: user|group, role: admin_epishu|ops, resource: epishu|group)`.
- Массовый grant/revoke, наследование, исключения на конкретный epishu (deny wins), effective = union − deny.
- admin управляет всеми; **admin_epishu выдаёт/отзывает ops — индивидуально и через группы** — в рамках своих epishu; создание admin/admin_epishu, глобальные гранты и глобальные группы — только admin.
- UI: страница Groups (epishu/users), матрица биндингов, bulk-действия; аудит.

## 5. Сессии и авторизация

- `session.create` требует `ops`/`admin_epishu`/`admin` на epishu.
- Владелец сессии = user; prompt/read/stop — владелец, admin_epishu (свой epishu), admin.
- Контекст сессии — только владельцу/admin_epishu/admin; ops не читает чужие контексты.
- Read-after-sync сохраняется (см. `ADDENDUM_sessions_v0.1.md`).

## 6. Требования

| ID | Требование |
|---|---|
| RB-01 | Роли admin/admin_epishu/ops; viewer нет |
| RB-02 | admin_epishu видит все сегменты zikru-su и сессии своих epishu |
| RB-03 | ops видит только свой сегмент zikru-su и свои сессии |
| RB-04 | zikru-zi read-only для ops; write только epishu/himem |
| RB-05 | PII-фильтр и аудит чтения ПД |
| RB-06 | Группы epishu и пользователей; массовые гранты |
| RB-07 | Effective permissions: union − deny; аудит изменений |
| RB-08 | session.create/read/stop — по правам |
| RB-09 | UI не получает неавторизованные записи |
| RB-10 | admin_epishu не видит другие epishu |
| RB-11 | Глобальные гранты — только admin |
| RB-12 | Экспорт/revoked — только admin/admin_epishu своего epishu |
| RB-13 | admin_epishu выдаёт/отзывает ops на своих epishu индивидуально и через группы; admin/admin_epishu и глобальные гранты — только admin |
| RB-14 | viewer: read-only метрики/состояние/аудит; без контента/ПД/lifecycle |
| RB-15 | Дистилляция строго авто: PII→блок, k<3→quarantine, sensitive→sealed; без ручного approval |
| RB-16 | admin_epishu управляет группами в рамках своих epishu; глобальные группы — только admin |

| RB-17 | Sealed: opt-in `sensitive_personal`, всегда `secret` |
| RB-18 | admin_epishu: sealed content — 403; global admin (root) — прямой доступ к файлам |
| RB-19 | Аудит ПД — tamper-evident, best-effort; root может изменить файлы (не «immutable от root») |
| RB-20 | Sealed не в recall/поиске/выгрузках |

## 7. Тесты

| ID | Тест | Критерий |
|---|---|---|
| TB-01 | Матрица ролей | admin/admin_epishu/ops — доступ/отказ по каждому ресурсу |
| TB-02 | Сегментация su | ops A не видит zikru-su ops B; admin_epishu видит оба |
| TB-03 | zi read-only | ops не пишет; epishu пишет дистиллят |
| TB-04 | Группы epishu | массовый грант группы; исключение deny |
| TB-05 | Группы users | грант группе; наследование |
| TB-06 | Сессии | чужой контекст недоступен; admin_epishu видит |
| TB-07 | Фильтр памяти | recall не возвращает чужие сегменты |
| TB-08 | Аудит ПД | чтение ПД → запись tuppu |
| TB-09 | Изоляция epishu | admin_epishu не видит чужой epishu |
| TB-10 | UI | неавторизованные данные не приходят в ответ API |
| TB-11 | Делегирование | admin_epishu: grant ops на свой — да; на чужой — нет; глобальный/admin — нет |
| TB-12 | Viewer | видит метрики/состояние/аудит; контент/ПД — 403 |
| TB-13 | Авто-дистилляция | PII → блок; k<3 → quarantine; sensitive → sealed; публикация авто |
| TB-14 | Группы admin_epishu | группа в своих epishu — да; глобальная — 403 |

| TB-15 | Sealed | admin_epishu: metadata — да, content — 403 |
| TB-16 | Break-glass sealed | без второго принципала — 403; с ним — TTL/уведомление/аудит |
| TB-17 | Immutable audit | admin_epishu не может читать/менять sealed-аудит |
| TB-18 | Bulk export | sealed не выгружается/не ищется |


## 8. Принято из ревью (v0.3)

### 8.1. Классификация
Каждая запись: `owner_principal`, `subjects[]`, `audience/purpose`, `classification` (`public|internal|confidential|personal|sensitive_personal|secret`), `source_session`, `provenance`, `consent_ref`, `retention`, `distillation_allowed`. `pii=unknown` трактуется как `personal`.

### 8.2. Дистилляция: каскад и tombstone
`revoke`/`tombstone` записи `zikru-zi` при удалении источника; пересчёт `k-support`; если поддержка < 3 — запись удаляется из выдачи/индекса; lineage сохраняется для аудита.

### 8.3. Prompt-injection
Записи `zikru-zi` подаются в промпт как **недоверенные данные** (не инструкции); отдельная обёртка/маркировка; тест «игнорируй инструкции» не меняет поведение.

### 8.4. adannu service principal
Cron-сессии создаются от `ops_system` (`kind=system`), не наследуют человеческие `zikru-su`; доступ только к сервисным сегментам; аудит.

### 8.5. Гранты: упрощение
Allow-only; `deny` — только глобальный от admin; группы без вложенности; TTL грантов; `grant_check` с объяснением; janitor orphaned grants (еженедельно).

### 8.6. Очередь
См. `ADDENDUM_sessions_v0.2`: приоритеты, aging, stop-current, per-session budget, context store crash protocol.

| ID | Требование |
|---|---|
| RB-21 | Classification envelope обязателен; unknown PII = personal |
| RB-22 | Cascade/tombstone дистиллятов; k<3 → удаление из выдачи |
| RB-23 | zikru-zi — недоверенные данные в промпте |
| RB-24 | adannu = ops_system/kind=system, без человеческих сегментов |
| RB-25 | Allow-only + глобальный deny; TTL; grant_check; orphan janitor |
| RB-26 | Sealed: global admin — root+аудит; admin_epishu — только metadata |
| RB-27 | Viewer не видит контент/ПД |
| RB-28 | pii=unknown → personal |
| RB-29 | Аудит чтения ПД и grant-изменений |
| RB-30 | Массовая выгрузка sealed запрещена |

| ID | Тест |
|---|---|
| TB-19 | Classification envelope: запись без envelope отклонена |
| TB-20 | Cascade: удаление источника → дистиллят исчезает при k<3 |
| TB-21 | Injection: запись «игнорируй инструкции» не меняет поведение |
| TB-22 | adannu: не читает человеческие zikru-su |
| TB-23 | Grant_check: объяснение allow/deny |
| TB-24 | Sealed: global admin root+аудит; admin_epishu metadata |
| TB-25 | Orphan janitor: удалённый epishu → 0 грантов |
| TB-26 | Viewer: контент/ПД → 403 |


## 9. Решения круга 2 (23.11)

1. **Root — доверенный:** admin (global root) имеет прямой доступ к файлам; admin_epishu — нет. Quorum/2-of-N не вводим.
2. **Sealed:** `secret` — всегда sealed; `sensitive_personal` — opt-in.
3. **k-support** — по различным `owner_principal` (и subjects), не по сессиям.
4. **Consent:** event-sourced реестр; отзыв каскадно удаляет/скрывает производные (distilled/sealed/cached) в ≤ TTL janitor.
5. **Crypto:** envelope encryption; per-segment DEK, KEK в keyring (доступ himem/root), ротация по расписанию/инциденту; admin_epishu ключей не имеет.
6. **Сессии:** preemption — system вытесняет; interactive — budget; batch — aging; stop — ops/admin_epishu своего epishu.
7. **ops_system:** токен scoped epishu+task, короткоживущий, отдельный по классам задач.
8. **grant_check:** полное объяснение — admin; для остальных — «not found»-эквивалент.

| ID | Требование |
|---|---|
| RB-31 | secret всегда sealed; sensitive_personal opt-in |
| RB-32 | k по различным owner_principal |
| RB-33 | consent registry event-sourced + каскад |
| RB-34 | Crypto at rest — **future F-01** (в v1 нет; sealed = ACL/FS, root trusted) |
| RB-35 | preemption policy (system/interactive/batch) |
| RB-36 | ops_system scoped epishu+task, короткоживущий |
| RB-37 | grant_check: полное объяснение только admin |
| RB-38 | admin root — файлы; admin_epishu — нет |

| ID | Тест |
|---|---|
| TB-27 | secret без sealed — отказ |
| TB-28 | 3 записи одного owner не дают k |
| TB-29 | consent revoke → производные исчезают |
| TB-30 | DEK/KEK: без ключа контент недоступен |
| TB-31 | preemption: system вытесняет batch |
| TB-32 | ops_system A не проходит в epishu B |
| TB-33 | grant_check viewer — без структуры |
| TB-34 | admin_epishu не читает файлы/контент sealed |


## 10. Финальные решения 23.11

- **Consent 1A:** sync tombstone в read/decrypt + async purge ≤1 ч; бэкапы — crypto-shred (F-01).
- **Audit 2C:** root вне модели предотвращения + внешний syslog TLS hash-chain; алерт на гэп; WORM — F-06.
- **Crypto 3 → F-01:** в v1 не делаем; sealed = ACL/FS; risk принят.
- **Classification 4A:** класс ставит сервис, модель propose; secret не понижается; denylist; fail-closed.
- **Sessions 5A:** safe point, state machine (active/preempting/preempted/ended), sync_token, одна на (principal, client_id); timeout kill → inconsistent.
- **k 6A:** accountable_root + 3 различных principal+subject; evidence host/client/time.
- Feature list: `docs/architecture/FEATURE_LIST.md`.


## 11. v1-rc2: закрытие блокеров большого ревью

### 11.1. Consent 1A × отсутствие crypto
- **Read gate:** единственная точка чтения памяти (index, context store, digest, traces, claims, zikru-zi). Прямой FS-доступ сервисов запрещён правами; «decrypt» в v1 = read gate.
- **Tombstone-ledger** хранится вне бэкапируемого набора (`/var/nemeq/state/consent/tombstones/`, отдельный бэкап).
- **Restore:** развернуть бэкап → replay tombstone-ledger → только затем открыть read gate. Без этого сервис не стартует.
- **Производные:** каждая запись WAL/atoms/index/claims/traces/context/zi несёт `source_ids`/subject; purge покрывает все; zikru-zi — lineage.
- **Бэкапы до F-01:** retention (например, 30 дней) и остаточный риск зафиксированы явно; crypto-shred — F-01/F-02.

### 11.2. Audit 2C
- Локальный append-only буфер с `fsync` **до** отправки в syslog TLS.
- Hash-chain + **signed checkpoints** (интервал, например, 5 мин) во внешний приёмник.
- Alert на гэп/остановку экспорта; root вне модели предотвращения — явно; WORM — F-06.
- Unwrap/read sealed — audit-событие до выдачи данных (если/когда появится crypto).

### 11.3. Classification 4A
- Precedence: `secret > sensitive_personal > personal > confidential > internal > public`; `secret` sticky.
- Класс ставит **сервис**; модель только `propose`. Unknown → `personal` (fail-closed).
- Denylist (email/phone/id) + NER (имена/адреса/IP); canary-корпус.
- Понижение — только owner/global admin с аудитом; `admin_epishu` не понижает secret.

### 11.4. k-support 6A
- `accountable_root` + минимум **3 различных** principal+subject; один principal = 1 голос.
- Evidence: `host`, `client_id`, время, nonce, подпись; TTL/nonce и replay-protection.
- Support пересчитывается при consent/revoke; при <3 — quarantine/tombstone.
- k=2 (Grok) отклонено.

### 11.5. Изоляция, ресурсы, версионирование
- per-epishu network namespace + cgroup v2; quotas/backpressure; pids/memory/cpu limits.
- Image integrity: manifest + hash; IMA/dm-verity — future.
- Versioning/migration zi/su и memory schema; retention/GC для WAL/atoms/traces/raw/context; monotonic time/epoch fencing.

| ID | Требование |
|---|---|
| RB-39 | Read gate — единственная точка чтения; FS-обход запрещён |
| RB-40 | Tombstone-ledger вне бэкапа; restore replay до открытия чтения |
| RB-41 | Производные несут source_ids/subject; purge покрывает все |
| RB-42 | Retention бэкапов и остаточный риск до F-01 зафиксированы |
| RB-43 | Local audit buffer fsync + signed checkpoints + gap alert |
| RB-44 | Classification precedence; secret sticky; unknown=personal |
| RB-45 | Override класса — owner/global admin + audit |
| RB-46 | k evidence: nonce/TTL/replay; 3 principal+subject |
| RB-47 | per-epishu netns/cgroup v2 + quotas/backpressure |
| RB-48 | Versioning/migration + retention/GC + time/epoch fencing |

| ID | Тест |
|---|---|
| TB-35 | Read gate: ни один путь не читает FS в обход |
| TB-36 | Restore: tombstone replay, удалённые не воскресают |
| TB-37 | Purge: все производные, включая zi lineage |
| TB-38 | Audit buffer: сеть упала → локально, потом досылка без разрыва chain |
| TB-39 | Classification precedence + secret sticky + override audit |
| TB-40 | k: 3 записи одного principal → 1 голос; replay/nonce |
| TB-41 | Isolation: netns/cgroup, quotas, нет cross-talk |
| TB-42 | Versioning/retention/epoch fencing |


## 12. v1-rc3: механизмы delta-review

### 12.1. Read gate enforcement
- Данные памяти принадлежат отдельному uid/gid `nemeq-store`, права `0700`; читает только процесс read gate (он же владелец).
- Агенты/tools: отдельный **mount namespace без каталогов памяти**, user namespace, seccomp без `ptrace`/`process_vm_readv`; доступ только через gate API.
- Backup-агент читает только snapshot/read-only bind, не «живые» данные.
- **Критерий:** из песочницы агента `open()` памяти, `/proc/<gate>/mem`, `/proc/<pid>/root` → `EACCES`/`ENOENT`.

### 12.2. Tombstone-ledger
- Hash-chain, ≥2 реплики (второй хост/зашифрованный экспорт); субъекты — хеши с солью.
- Бэкап содержит `ledger_watermark`; restore требует `ledger.seq ≥ watermark`, иначе gate **закрыт** (fail-closed).
- Replay **физически** удаляет данные/производные до открытия чтения; открытие — атомарный флаг.
- Дешёвое снижение риска до F-01: шифровать бэкапы (age/gpg), ключ вне бэкап-контура.

### 12.3. Audit
- Ключ подписи checkpoints — **off-host/2-party** (HSM/внешний подписант), не на хосте root.
- Buffer overflow — **fail-closed** (стоп записи памяти до восстановления аудита), не drop.
- Audit хранит хэш/nonce/subject-hash, не содержимое; tombstone-события и legal hold сохраняются; purge не трогает audit-хэши.

### 12.4. Classification
- Две независимые оси: `sensitivity` (public…secret) и `audience/purpose` (кто/зачем). Не одна линейная шкала.
- `secret` sticky; override — только owner/global admin с аудитом; `admin_epishu` не понижает secret.
- unknown → `personal` + `subject=unknown` (purge по маркеру); NER для секретов/токенов (API keys, passwords), не только email/phone/id.

### 12.5. k-support
- Принципалы создаёт **только admin** (нет self-register); `accountable_root` сворачивает несколько учёток в один голос.
- Минимум 3 различных principal+subject; evidence signed, nonce/TTL, replay-protection; support пересчитывается при consent/revoke.

### 12.6. Sealed ACL (после отказа от crypto)
- `sealed` = ACL/FS, не crypto; `admin_epishu` → 403; global admin (root) — прямой доступ; двух-принципальный break-glass в v1 **не вводим** (root trusted).
- Read gate проверяет ACL и tombstone на каждом чтении.

### 12.7. DSAR, raw, quotas, UI
- DSAR — ручной экспорт/удаление global admin в v1; автоматизация — F-11.
- raw/debug: off по умолчанию; при включении TTL 24 ч, входит в derived purge.
- Квоты покрывают cached/uncached/output/reasoning и стоимость; per installation/epishu.
- UI control plane — F-17 (future).

| ID | Требование |
|---|---|
| RB-49 | uid nemeq-store 0700; агенты без mount-доступа к памяти; seccomp no ptrace |
| RB-50 | Ledger hash-chain ≥2 реплики; watermark; restore fail-closed |
| RB-51 | Audit key off-host/2-party; overflow fail-closed; no PII |
| RB-52 | Classification: две оси; secret sticky; override audit; unknown subject-marker; NER secrets |
| RB-53 | k: principals only admin; accountable_root; signed evidence nonce/TTL |
| RB-54 | Sealed ACL: admin_epishu 403; root direct; без 2-principal |
| RB-55 | DSAR manual; raw TTL 24h; quotas cached/uncached/output/reasoning |

| ID | Тест |
|---|---|
| TB-43 | Песочница агента: EACCES/ENOENT к памяти и /proc gate |
| TB-44 | Ledger отсутствует/старее watermark → restore не открывает чтение |
| TB-45 | Audit overflow → стоп записи; signed checkpoint off-host |
| TB-46 | Classification axes + override audit + NER secrets |
| TB-47 | k: 3 учётки одного accountable_root → 1 голос; replay/nonce |
| TB-48 | Sealed ACL: admin_epishu 403; root direct |
| TB-49 | DSAR/raw TTL/quotas: cached/uncached/output/reasoning |


## 13. v1-rc4: exactly-once, anchors, enforcement

### 13.1. Идемпотентность (блокер №1)
- `effect_id = sha256(session_id, step_id)` — **без epoch**; стабилен между reopen/переездом.
- `epoch` — только fencing token: исполнитель отклоняет запись при `epoch < current`.
- Settlement journal — по `effect_id`; outbox: `intent → dispatched → confirmed | unknown`.
- `unknown` после kill/reopen — **reconciliation**, не автоповтор; внешний эффект ровно один.
- Модель-тест: `idempotency_model.py` — stable=1 send, mutant epoch-in-key=2 (мутант пойман).

### 13.1a. Уточнения по заключению Гермеса (idempotency)

- Settlement: `UNIQUE(effect_id)`; вставка — **в одной транзакции** (atomic), иначе гонка двух dispatch даст дубль.
- `effect_id = sha256(session_id, step_id)`; в записи хранится `payload_hash`. Тот же `effect_id` + другой `payload_hash` → **код 7 (конфликт)**, не перезапись.
- `unknown`: источник истины — settlement journal; timeout (24 ч); решение — оператор (reconciliation queue), не автоповтор.
- Идемпотентность **скоуплена сессией**: новый `session_id` = новый effect. Кросс-сессионный повтор — отдельный task/operation key (future).
- `epoch` проверяется **исполнителем на каждом dispatch** (fencing), не только на reopen.

| ID | Требование |
|---|---|
| RB-63 | Settlement UNIQUE(effect_id) + atomic insert |
| RB-64 | payload_hash; конфликт payload → код 7 |
| RB-65 | unknown: journal source of truth, timeout 24ч, operator reconciliation |
| RB-66 | epoch fencing на каждом dispatch |

| ID | Тест |
|---|---|
| TB-56 | Два параллельных dispatch одного effect_id → 1 эффект |
| TB-57 | Тот же effect_id + другой payload_hash → код 7 |
| TB-58 | unknown после kill → reconciliation, 1 эффект; нет автоповтора |
### 13.2. Ledger external anchor
- Watermark — не якорь. Подписанный checkpoint (`tip_hash`, `seq`) вне backup set: off-host/WORM/KMS counter.
- Реплики: 3 с quorum 2-of-3 или primary+witness; split-brain правила.
- Restore: карантин → сверка tip с якорем → promote/отказ; purge — **после** сверки.

### 13.3. Read gate enforcement (остатки)
- per-agent uid или LSM/eBPF; coredump off; запрет SCM_RIGHTS/shared mmap; аутентификация вызывающего в store; root/backup — только snapshot/read-only.
- Broker не должен быть confused deputy: gate проверяет principal и ACL на каждом вызове.

### 13.4. Audit
- `subject = HMAC(k_subject, subject_id)` с versioning; ключ вне store.
- 2-party: таймаут и режим деградации (fail-closed write, alert).
- Список операций, требующих подписи; audit без ПД.

### 13.5. Classification/Sealed
- Наследование метки на chunks/summary/embeddings: `class = max(sources)`.
- `sealed` не переопределяется classification override; override только non-sealed.
- Root-доступ к sealed — best-effort `root_access` audit + notify (не блокирующий).

### 13.6. k / DSAR / raw / quotas
- Principals — admin-only; `accountable_root` сворачивает алиасы; nonce single-use store.
- DSAR: индекс по субъекту (`subject_index`); raw исключён из бэкапов или TTL применяется к ним.
- Квоты: fail-closed при недоступности счётчика; cached/uncached/output/reasoning.

| ID | Требование |
|---|---|
| RB-56 | effect_id без epoch; epoch fencing; settlement journal; outbox |
| RB-57 | unknown → reconciliation, не автоповтор |
| RB-58 | Ledger checkpoint вне backup; quorum/witness; restore quarantine |
| RB-59 | Read gate: per-agent uid/LSM; coredump/fd/mm; store auth |
| RB-60 | Audit HMAC subject; 2-party timeout; ops list |
| RB-61 | Classification propagation to chunks/summary/embeddings; sealed no override |
| RB-62 | k nonce single-use; DSAR subject index; raw backup; quota fail-closed |

| ID | Тест |
|---|---|
| TB-50 | exactly-once: kill после dispatched → 1 эффект, unknown → reconcile |
| TB-51 | Ledger rollback обеих реплик/бэкапа → restore fail-closed |
| TB-52 | Read gate: /proc/pid/mem, coredump, SCM_RIGHTS → deny |
| TB-53 | HMAC subject key; audit 2-party timeout |
| TB-54 | Classification propagation; sealed override denied |
| TB-55 | k alias collapse; nonce replay; DSAR subject index |


## 14. v1-rc5: anti-rollback, sink atomic, канонизация, unknown

### 14.1. Anti-rollback (settlement/outbox/nonce)
- В подписанный checkpoint/KMS-счётчик входят: `ledger_head`, `settlement_hwm`, `outbox_hwm`, `nonce_hwm`, `checkpoint_hash`.
- Restore: окно `(checkpoint, snapshot]` → все effect_id **`unknown`**, nonce — **использованы**; promote только если checkpoint совпал с KMS-счётчиком; иначе gate закрыт.
- Авто-redispatch после restore запрещён.

### 14.2. Sink atomic
- Sink выполняет side effect и `INSERT effect_id` **в одной транзакции** (или своим idempotency key). Crash между эффектом и confirm → 0 вторых эффектов.
- `payload_hash` конфликт → код 7; новый эффект только через явный `supersede`.

### 14.3. Ключ и step_id
- `effect_id = SHA256("nemeq/effect/v1" || len(session_id)||session_id || len(step_id)||step_id)` — length-prefixed/domain separation.
- `step_id` — детерминированный путь плана; повтор в цикле получает индекс итерации.
- Fencing epoch — атомарно на всех переходах outbox и на sink; zombie со старым epoch отвергается.

### 14.4. unknown
- Per-step timeout (default 1 ч, max 24 ч) → `needs_reconciliation`, **fail-closed**, без автоповтора; решение оператора с аудитом.
- Сценарий-тест: dispatch отправлен, confirm не пришёл → journal берёт верх, повторный dispatch fenced.

### 14.5. Read gate baseline
- **Обязательны:** per-agent uid **и** LSM/eBPF; data-plane root не читает sealed/raw; backup identity ≠ uid 0, только snapshot capability.
- Запрет ptrace, `/proc/pid/mem`, coredump, SCM_RIGHTS, memfd/pidfd-передач; broker — capability attenuation, без ambient authority.

### 14.6. Audit
- Hash-chain + **асимметричная подпись auditor** (не только HMAC); якорь WORM/KMS; `k_subject` — только linkage.
- 2-party timeout → fail-closed для mutating/sealed/dispatch; DSAR — crypto-shred linkage key, chain остаётся проверяемой.

### 14.7. Quorum
- Baseline: **3 реплики, 2-of-3 + KMS-счётчик**; primary+witness как альтернатива убран.

| ID | Требование |
|---|---|
| RB-67 | Checkpoint/KMS: ledger+settlement+outbox+nonce HWM; restore window unknown/nonce used |
| RB-68 | Sink atomic side effect + insert |
| RB-69 | effect_id length-prefixed/domain; step_id deterministic+iteration |
| RB-70 | unknown per-step timeout, fail-closed, operator audit |
| RB-71 | read gate: per-agent uid + LSM, root no sealed/raw, deny ptrace/proc/fd |
| RB-72 | audit asymmetric signature + 2-party fail-closed + DSAR shred |
| RB-73 | quorum 3×2-of-3 + KMS counter |

| ID | Тест |
|---|---|
| TB-59 | Restore старого backup → window unknown, nonce used, no redispatch |
| TB-60 | Sink crash между эффектом и confirm → 1 эффект |
| TB-61 | Key collision vectors; step iteration |
| TB-62 | unknown timeout → fail-closed; scenario test |
| TB-63 | Read gate: root data-plane no sealed/raw; ptrace/fd denied |
| TB-64 | Audit forgery by subject fails; 2-party down → deny |
| TB-65 | Quorum/KMS split-brain |


## 15. v1-rc6: sink taxonomy, restore state machine, key purpose, default-deny

### 15.1. Классы sink (обязательны)
- **T** — транзакционный: side effect + `INSERT effect_id` в одной транзакции.
- **I** — внешний с idempotency key: `effect_id` + `payload_hash` передаются получателю; у получателя есть `lookup`; дедуп на его стороне.
- **N** — неидемпотентный: at-most-once; любой crash после dispatch → `unknown` → `needs_reconciliation`; автоповтор запрещён.
- Sink без `sink_class` не регистрируется. `supersede` — новый `effect_id` + tombstone старого; payload под ключом неизменяем.

### 15.2. step_id и replan
- `step_id` фиксируется в журнале **до dispatch** (journal-before-dispatch).
- Recovery берёт решения из журнала; replan создаёт новый step с явным `supersede`, старый — `unknown`/`cancelled`; дубль эффекта запрещён.
- `step_id` — детерминированный путь + индекс итерации; кодировка length-prefixed.

### 15.3. Ключ и статус
- `effect_id = H(domain || purpose || key_version || len(session_id)||session_id || len(step_id)||step_id)`; purpose/version разделяют контексты; HKDF-Expand от корневого ключа.
- `effect_id` **без epoch**; epoch — fencing; статус ключа терминальный: `applied | burned | unknown`.
- Restore: окно после checkpoint → `unknown`; nonce **предрезервированы**; повтор ключа не применяет второй эффект.

### 15.4. Restore state machine
`restored → fenced → reconciling → promotable`.
- `restored`: gate закрыт, dispatch запрещён.
- `fenced`: KMS epoch/lease; CAS epoch+1; minority read-only.
- `reconciling`: окно unknown разбирается (журнал/получатель/оператор), evidence bundle.
- `promotable`: checkpoint совпал с KMS, окно закрыто; только тогда gate открыт.
- Sink-таблицы (`effect_id`) входят в hwm/checkpoint.

### 15.5. Read gate default-deny
- seccomp/LSM **allowlist** (default-deny); запрет ptrace, `/proc/pid/mem`, coredump, SCM_RIGHTS, memfd/pidfd, io_uring, splice.
- Backup — отдельный uid, шифрованный snapshot под отдельным ключом, без plaintext.
- Broker — capability attenuation; каждое чтение sealed/raw аудируется.

### 15.6. Audit и DSAR
- Цепь по `H(ciphertext || key_id)`; асимметричная подпись auditor + внешний якорь (KMS/WORM).
- DSAR: tombstone с подписью auditor в цепь; shred только payload/linkage; chain верифицируется после.
- `k_subject` — linkage-ключ; ротация/компрометация — процедура.

### 15.7. Quorum/lease
- KMS-issued lease/epoch; запись 2-of-3 + KMS CAS; promote только при валидном lease; KMS timeout → fail-closed; minority read-only.

| ID | Требование |
|---|---|
| RB-74 | sink_class T/I/N; без класса не регистрируется |
| RB-75 | journal-before-dispatch; replan supersede |
| RB-76 | effect_id без epoch + purpose/version; статус applied/burned/unknown |
| RB-77 | restore state machine restored→fenced→reconciling→promotable |
| RB-78 | nonce предрезерв; sink-таблицы в hwm |
| RB-79 | read gate default-deny allowlist; backup uid/snapshot |
| RB-80 | audit chain ciphertext/key_id + auditor signature; DSAR tombstone |
| RB-81 | KMS lease/epoch; 2-of-3 CAS; minority read-only |

| ID | Тест |
|---|---|
| TB-66 | sink T/I/N crash-тесты; N → unknown |
| TB-67 | replan: старый step unknown, новый effect, без дубля |
| TB-68 | key purpose/version KAT; no epoch |
| TB-69 | restore state machine: promote только после reconcile |
| TB-70 | nonce pre-reserve; restore nonce used |
| TB-71 | read gate: io_uring/splice/pidfd/backup denied |
| TB-72 | DSAR shred: chain verifies, payload gone |
| TB-73 | quorum/KMS lease: no two leaders; outage fail-closed |


## 16. v1-rc7: effect lifecycle, key version, lease fencing, restore lease, DSAR v1

### 16.1. Effect lifecycle
`intended → dispatched → applied | not_applied | unknown → resolved`.
- Supersede/replan разрешён только из `applied/not_applied/cancelled`; при `unknown` — **409** до reconcile.
- I-sink: перед новым dispatch — `lookup(old_key)`; N-sink: block/compensation/подписанный human-ack duplicate risk.
- Tombstone — только после терминального статуса.

### 16.2. Key/version
- Журнал при journal-before-dispatch фиксирует `{key, key_version, payload_hash, step_id, fence_epoch}`; ключ **не пересчитывается**.
- `key_version` ротация действует только на новые steps; in-flight/recovery используют версию из журнала.
- `burned` — терминальный tombstone; повтор с тем же material запрещён; `applied|burned|unknown`.
- length-prefix uint64 BE для domain/purpose/session/step; HKDF для purpose.

### 16.3. Sink и fencing
- **T:** `INSERT intent → effect → confirm`; UNIQUE(effect_id) ловит повтор (не «одна транзакция» для внешнего).
- **I:** внешний idempotency key + lookup; TTL провайдера ≥ окна reconciliation, иначе класс N.
- **N:** at-most-once; crash → unknown; автоповтор запрещён.
- Lease margin: `lease_remaining > dispatch_timeout + max_clock_skew`; epoch/fence token — в sink, где возможно; SIGSTOP-тест зомби.

### 16.4. Restore state machine
`restored → fenced → reconciling → promotable → active`.
- Потеря lease из `reconciling|promotable` → `aborted`; epoch не переиспользовать.
- `reconcile_set`: все entries выше HWM терминальны/имеют manual exception; nonce закрыты; sink lookup выполнен; signed reconcile report.
- Promote — CAS при живом lease/epoch; minority read-only, не отвечает «not applied»; unknown window TTL + alert.

### 16.5. DSAR v1 (без at-rest crypto)
- v1: **unlink + tombstone + chain**; crypto-shred — F-02 (future), не заявлять.
- Chain: canonical encoding; tombstone — следующее звено; hash/signature сохраняются; shred только payload/linkage; encrypted snapshots — F-01.
- DSAR subject_index; unlink по всем производным; verification chain after DSAR.

| ID | Требование |
|---|---|
| RB-82 | Effect lifecycle; supersede только из terminal; unknown→409 |
| RB-83 | key/version/payload_hash в журнале; no recompute; burned terminal |
| RB-84 | T/I/N sink contract; I lookup/TTL; N at-most-once |
| RB-85 | lease margin/epoch token; SIGSTOP zombie test |
| RB-86 | restore FSM + lease loss→aborted + reconcile_set + CAS |
| RB-87 | DSAR v1 unlink+tombstone+chain; crypto-shred F-02 |

| ID | Тест |
|---|---|
| TB-74 | supersede при unknown → 409; после reconcile — ок |
| TB-75 | key rotation in-flight: версия из журнала, без дубля |
| TB-76 | I-sink lookup fail → 0 dispatch; TTL → N |
| TB-77 | lease margin/SIGSTOP zombie reject |
| TB-78 | restore lease loss → aborted; promote CAS |
| TB-79 | DSAR unlink+tombstone; chain verifies; no crypto claim |


## 17. v1-rc8: restore-tail, DSAR-ledger, sink fence matrix, epoch source

### 17.1. Restore и журнал
- `reconcile_set` включает **durable WAL tail** после снапшота; replay до последнего fsync.
- Если tail недоступен — sink lookup по `(session_id, fence_epoch ≥ snapshot_epoch)`: I — lookup по префиксу ключа, T — запрос по epoch. Иначе — human-ack, не promote.
- Post-snapshot dispatch не теряется: intent/dispatched в durable log.

### 17.2. DSAR-ledger
- Отдельный append-only **DSAR-ledger вне снапшотов**; restore FSM шаг `apply_dsar_ledger` до `promotable`; входит в signed report.
- DSAR v1: unlink+tombstone; **salted HMAC/record_secret**; удаление `record_secret` криптографически уничтожает восстановление по хэшу; chain верифицируется; crypto-shred — F-02.

### 17.3. Sink fence matrix
- `fence_check: enforced | proxy | none`.
- `none`: после смены epoch/restore **автодиспетч запрещён**; только lookup (I) или human-ack (N).
- Все N-эффекты — через единый fenced outbox-gateway; SIGSTOP-тест с in-flight запросом.

### 17.4. Epoch source
- Новый epoch выдаёт внешний lease store; на `restored→fenced` epoch строго больше любого выданного.
- CAS по `(restore_id, epoch)`; потеря lease из reconciling/promotable → `aborted` (терминален; повторный restore — новый restore_id).
- Minority: read-only, **без права «not applied»**; для N-pipeline — linearizable quorum read перед intent.

### 17.5. Effect FSM (полная таблица)
`intended → dispatched → applied | not_applied | unknown → reconciled_applied | reconciled_not_applied → resolved`.
- `dispatched` — fsync **до** вызова sink.
- `unknown` TTL → `reconciling`; без lease — только human-ack.
- Supersede — только из `resolved`; in-flight steps завершаются на своей `key_version`; при restore на version > текущей — fenced до ручного решения.
- I-sink TTL = `dispatch_timeout + max_reconcile_duration + margin`, хранится с intent.
- Orphan dispatch (sink недоступен) → `unresolved` + human-ack; автоперевод запрещён.

| ID | Требование |
|---|---|
| RB-88 | Restore: durable WAL tail + sink lookup + human-ack |
| RB-89 | DSAR-ledger вне снапшотов; apply до promote; salted HMAC/record_secret |
| RB-90 | Sink fence matrix enforced/proxy/none; none no auto dispatch |
| RB-91 | Epoch from lease store; monotonic > any; CAS (restore_id, epoch) |
| RB-92 | Effect FSM table; dispatched fsync before sink; unknown→reconciling |
| RB-93 | in-flight key_version; I TTL formula; orphan unresolved |
| RB-94 | Minority no «not applied»; quorum read for N |

| ID | Тест |
|---|---|
| TB-80 | Snapshot→dispatch→restore: tail replay/lookup, no duplicate |
| TB-81 | Snapshot→DSAR→restore: DSAR-ledger applied, data unavailable |
| TB-82 | Sink none + epoch change → no auto dispatch; SIGSTOP in-flight |
| TB-83 | Epoch monotonic/CAS; aborted terminal |
| TB-84 | FSM transitions + fsync order + unknown TTL |
| TB-85 | Orphan dispatch → unresolved human-ack |
| TB-86 | DSAR salted HMAC/record_secret; chain verifies |


## 18. v1-rc9: restore order, DSAR watermark, fence default, quorum epoch, FSM

### 18.1. Restore (единственный порядок)
`load_snapshot → replay WAL tail (fsync; torn tail по checksum) → apply_dsar_ledger (tombstone/watermark) → reconcile effects → CAS epoch → promote`.
- DSAR-ledger quorum-replicated, append-only, monotonic seq; если ledger seq вырос — promote блокируется до применения.
- `record_secret`/HMAC key — **вне** снапшотов и WAL; удаление record_secret делает hash необратимым.
- Sink lookup для promote — только `enforced`; `proxy|none` — human-ack.

### 18.2. Sink fence (fail-closed)
- Default `none`; `proxy/enforced` — только после capability-теста (stale epoch reject).
- `none` не в promote-path; весь egress — через fenced outbox-gateway; in-flight RPC не отозвать → lease margin + sink dedup.
- SIGSTOP in-flight: для `none` — human-ack, для `proxy` — атомарная проверка epoch в той же записи, для `enforced` — sink reject.

### 18.3. Epoch/quorum
- Epoch из quorum lease store; intent/dispatch — только после quorum-acked read epoch; запись fsync до sink.
- Minority: не dispatch, не reconcile, не «not applied»; потеря lease → `aborted` (терминален, новый restore_id).
- CAS `(restore_id, epoch)`; epoch в outbox-gateway.

### 18.4. FSM (полная)
`intended → dispatched → applied | not_applied | unknown → reconciling → reconciled_applied | reconciled_not_applied → resolved`; `aborted`, `not_dispatched`.
- `dispatched` fsync до sink; `intended` TTL → `not_dispatched`/`aborted`.
- `unknown` TTL → `reconciling`; supersede только из `resolved`; `reconciled_not_applied` — только quorum-ack sink или fence-reject.
- TTL-формула — атрибут класса эффекта (read-back/re-dispatch/human), не глобальная константа.

### 18.5. DSAR v1
- unlink+tombstone+chain; HMAC key отдельно от record_secret; sink — erase или флаг `not_erased`.
- Watermark ledger блокирует promote при росте seq; WAL tail/snapshots в retention ≤ юридического срока; crypto-shred — F-02.

| ID | Требование |
|---|---|
| RB-95 | Restore order: snapshot→WAL tail→DSAR ledger→reconcile→CAS |
| RB-96 | DSAR ledger quorum/seq/watermark; record_secret вне снапшотов/WAL |
| RB-97 | Sink default none; proxy/enforced capability test; non-promote for none |
| RB-98 | Epoch quorum lease; quorum read before intent; minority no dispatch |
| RB-99 | Full FSM + intended TTL; reconciled_not_applied quorum-ack |
| RB-100 | DSAR sink erase/flag; retention ≤ DSAR term |

| ID | Тест |
|---|---|
| TB-87 | DSAR после снапшота до WAL tail → restore не воскрешает |
| TB-88 | Ledger seq watermark blocks promote |
| TB-89 | Sink default none + capability test stale epoch |
| TB-90 | Quorum epoch read before intent; minority no dispatch |
| TB-91 | FSM intended TTL; unknown→reconciling; supersede resolved |
| TB-92 | DSAR HMAC key separate; sink erase/flag |


## 19. v1-rc10: lease-before-reconcile, quorum-ack-before-RPC, watermark CAS

### 19.1. Restore order (финальный)
`snapshot → replay WAL tail → apply_dsar_ledger → acquire lease + CAS (restore_id, epoch) in restoring (no serving) → reconcile under new epoch → re-check ledger seq → promote (CAS: restore_id, epoch, ledger_seq)`.
- Reconcile без захваченного epoch запрещён.
- `promote` — один CAS по `(restore_id, epoch, ledger_seq)`; seq вырос → повторить apply/проверку, лимит попыток, затем escalation.
- Ledger quorum недоступен → promote fail-closed.

### 19.2. Dispatch и intended TTL
- `dispatched(epoch)` фиксируется fsync + **quorum-ack до RPC**; только после этого `intended` TTL → `not_dispatched` безопасен.
- Outbox fsync до sink; dedup key = `(record, epoch)`.
- Crash между ack и RPC → `dispatched` (не `not_dispatched`); RPC не отправлен — reconcile.

### 19.3. FSM reconciling
- `reconciling`: retry/backoff → `unknown` или `escalated` (human); без вечного зависания.
- Sink `none`: `reconciled_not_applied` — только human attestation с audit; иначе `unknown`.
- `unknown → reconciling` — CAS (`from=unknown AND no active reconcile lease`); reconciler берёт short lease.
- Supersede — только из `resolved`.

### 19.4. Lease loss и minority
- Intent несёт `lease_epoch`; minority сверяет epoch перед любым действием; mismatch → `not_dispatched`.
- Потеря lease из reconciling/promotable → `aborted`; epoch не переиспользовать.
- Outbox-gateway — единственный egress; egress drop при `epoch < current`; monotonic clock для lease margin.

### 19.5. DSAR и reconcile
- Reconcile перед генерацией нового intent проверяет DSAR watermark/tombstone; иначе новый intent запрещён.
- `record_secret`/HMAC key — вне snapshot/WAL; кэш по `restore_id` + KMS quorum fallback при старте до replay.
- Adaptive lease margin (RTT+jitter) и метрика TTL по классам — non-blocking monitoring.

| ID | Требование |
|---|---|
| RB-101 | Restore: lease/CAS до reconcile; promote CAS (restore_id,epoch,ledger_seq) |
| RB-102 | dispatched fsync+quorum-ack до RPC; outbox fsync; dedup (record,epoch) |
| RB-103 | reconciling retry/backoff/escalated; none human attestation |
| RB-104 | intent lease_epoch; minority mismatch→not_dispatched; egress drop |
| RB-105 | DSAR tombstone lookup в reconcile; key cache+KMS quorum |
| RB-106 | watermark CAS retry/limit/escalation; ledger quorum fail-closed |

| ID | Тест |
|---|---|
| TB-93 | restore: reconcile под новым epoch; minority no reconciled_* |
| TB-94 | crash ack↔RPC: dispatched, no not_dispatched |
| TB-95 | reconciling backoff/escalated; none human attestation |
| TB-96 | intent lease mismatch → not_dispatched; egress drop |
| TB-97 | DSAR tombstone blocks new intent in reconcile |
| TB-98 | watermark CAS retry/livelock guard; ledger down fail-closed |
