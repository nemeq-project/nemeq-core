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
| RB-34 | envelope encryption: DEK/KEK, ротация, root/himem |
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
