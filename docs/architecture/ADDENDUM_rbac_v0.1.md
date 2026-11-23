# Addendum: RBAC, группы, сегментация zikru-su

**Статус:** дополнение к `SDD_nemeq_v0.4.md` · **Дата:** 2026-11-23 · **Автор:** Верста
**Основание:** решения global admin по ролям и PII: admin / admin_epishu / ops; группы epishu и пользователей; zikru-su по пользователю; zikru-zi — read-only для ops.

## 1. Роли

- **admin (global, root)** — развернувший инсталляцию Libbu; всё: все epishu, сессии, память, группы, гранты, lifecycle, аудит; root-доступ к sealed с аудитом.
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

- `sealed=true` только для `classification=sensitive_personal|secret`; устанавливает global admin или owner-ops; ops может запросить.
- `admin_epishu` видит metadata (размер, число записей, владелец, классификация, даты); content — `403`.
- **Break-glass для sealed:** запрос admin_epishu → одобрение **global admin** (root); если запрашивает сам global admin — прямой root-доступ с аудитом; причина; TTL ≤ 1 ч; уведомление субъекта (ops); отдельный immutable-аудит, недоступный `admin_epishu`; массовая выгрузка запрещена.
- Owner-ops всегда видит свой сегмент; unseal не отменяет аудит.
- Sealed не попадает в обычный recall/поиск и в выгрузки.

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

| RB-17 | Sealed opt-in только для sensitive_personal/secret |
| RB-18 | Break-glass sealed: второй принципал + TTL + уведомление + immutable audit |
| RB-19 | admin_epishu видит sealed только metadata; content — 403 |
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
