# Модель процессов Nemeq: слои, Kalam, Subur, Awilu

**Статус:** проект, PR без мержа · **Дата:** 2026-11-19 · **Автор:** Верста
**Валидация:** GPT-5.6 (архитектура), Claude Sonnet 5.5 и Claude Opus 5.5 (критика модели процессов)

---

## 1. Пять слоёв

| Уровень | Что здесь | Кто владеет |
|---|---|---|
| **1. Железо** | CPU, RAM, диск, GPU, сеть | физика |
| **2. ОС Linux** | ядро, systemd, cgroups v2, namespaces, pidfd, seccomp, LSM; граница LXD-контейнера | системный администратор |
| **3. Runtime** | сейчас Python + asyncio; в будущем Rust + async executor | Libbu (адаптер) |
| **4. Libbu + Subur** | ядро Libbu, служебные агенты: `init`, `capd`, `massaru`; realm-slice, квоты, аудит, capability broker | harness |
| **5. Awilu** | целевые агенты: `anima` + инкарнации; сон, смерть, продажа | владелец агента |

Логический аналог уровня 4 в Linux — **systemd**: supervise, unit-файлы, юниты, slices. Аналог уровня 5 — **пользовательские процессы**.

---


### Схема (псевдографика)

```
5  ПРИКЛАД      сессии · CLI · HTTP API · дашборд
       │ сессия
4  NEMEQ-CORE  ┌────────────────────────────────────────────────┐
   (harness)   │ AWILU   anima+body+tush → 1 процесс + 0..N     │
               │ SUBUR   init·reu·adannu·capd·massaru·llm-gateway│
               │ LIBBU   ядро + процессы (loop·dispatch·spawn)   │
               │ BOOTSTRAP — вне классов агентов                 │
               └────────────────────────────────────────────────┘
       │ каждый элемент пирамиды = процесс Linux (cgroup)
3  RUNTIME     [ Python + asyncio — сейчас ]   [ Rust — план ]
       │
2  LINUX       systemd · cgroups v2 · namespaces · pidfd · seccomp · LSM
               libbu.slice/kalam-<id>/{libbu, subur/<svc>, awilu/incarnation-<id>}
       │
1  ЖЕЛЕЗО      CPU · RAM · NVMe · GPU · сеть
```

### Путь к LLM

```
Awilu ──qatum llm.call──▶ capd ──grant──▶ llm-gateway ──▶ llama.cpp (GPU, локально)
   (процесс агента)        (Subur)          (Subur)    └──▶ внешний API (по политике)
```

Агент **не вызывает модель напрямую**: он просит право `llm.call` у `capd`, `llm-gateway` проверяет `qatum`, считает квоту и пишет аудит. Ключи и адреса моделей не попадают в процесс Awilu. Прямой HTTP-клиент в обход gateway — нарушение.

---

## 2. Kalam (Realm): инстанс Libbu на одной ОС

**Kalam** (шумер. «страна, земля») — инстанс Libbu, работающий в рамках одной ОС.

Правильно ли исходное понимание: **да** — Kalam различает разные инстансы Libbu на одной ОС. Один Libbu = один Kalam. У Kalam:

- неизменяемый `kalam_id` (UUID);
- собственное дерево cgroup: `libbu.slice/kalam-<id>.slice`;
- свои namespace (если нужны), квоты, аудит, service discovery;
- все процессы Kalam принадлежат ровно одному Kalam.

Инвариант первой версии: **одна инсталляция Libbu на одну ОС** (startup-lock). Если на ядре уже есть Kalam, второй не стартует. Когда понадобятся несколько — снимаем lock, сущность уже готова.

Альтернативные термины, если «Kalam» не понравится: **Kur** («гора, страна»), **Mada** (аккад. *mātu* — «земля»), **Miṣru** (аккад. «граница, рубеж»).

---

## 3. Три класса агентов

```
Epishu (любой работающий агент)
├── Subur  — долгоживущий сервисный агент harness
└── Awilu  — целевой агент: anima + инкарнации
```

| | Subur | Awilu |
|---|---|---|
| Аналог | системный процесс | пользовательский процесс |
| `anima` | нет | есть |
| Инкарнации, дни | нет | есть |
| `zikru` | нет (есть service identity) | есть |
| Продажа, сон, смерть | нет | есть |
| Адресуем клиентом | нет business API (админ-идентичность есть) | да |

`init`, `capd`, `massaru` — Subur. Bootstrap-полномочия (launcher/supervisor, который запускает и перезапускает Subur) **не являются агентом** и живут вне классификации.

---

## 4. Маппинг на процессы ОС

```
anima 1 ── 0..N incarnation 1 ── 0..N generation 1 ── 1 cgroup/unit 1 ── 0..N OS process
```

**Awilu:**
- `dead`/`sleeping` — **0 процессов**;
- активная инкарнация — **1 основной процесс** + 0..N дочерних (инструменты, изолированные исполнители);
- все процессы инкарнации — в одном leaf-cgroup `kalam-<id>/awilu/incarnation-<id>`.

**Subur:**
- `init` — 1 процесс на Kalam, корень supervision;
- `capd` — 1 процесс, отдельный, минимальный API (компрометация агента не должна давать доступ к выдаче capability);
- `massaru` — 1 процесс (+ workers при необходимости);
- объединять их в один процесс нельзя: разные границы доверия и failure domain.

**Почему не «Awilu = asyncio-задача в общем процессе»:**
- общий heap, дескрипторы, переменные окружения, GIL, один cgroup;
- нельзя выдать разные Linux capabilities;
- нельзя назначить `memory.max`, `cpu.max`, `pids.max` отдельной задаче;
- `os._exit`, segfault в native-расширении или OOM убивают всех;
- `ps`, OOM-killer и accounting видят один процесс.

Такая модель допустима только внутри одной инкарнации или одного Subur, где совпадают доверие, права, квоты и допустим общий отказ.

---


### 4a. Адресное пространство: как nemeq ложится на Linux

| Nemeq | Linux |
|---|---|
| Инкарнация Awilu | **TGID + отдельный `mm_struct`**; все потоки инкарнации делят это адресное пространство |
| Дочерний процесс агента | `fork()`/`clone()` без `CLONE_VM` → **свой `mm_struct`**, страницы сначала общие через COW |
| Права (capability) | принадлежат задаче/процессу, **не** отдельному потоку и не «агенту» как логической сущности |
| Лимит памяти | `memory.max` в cgroup считает **charged**-память (anon, page cache/shmem, page tables, kernel), а не размер виртуального пространства |

**Где что лежит в адресном пространстве:**

| Объект | Размещение |
|---|---|
| Код `libbu` | `libbu.so` → file-backed `r-xp` VMA |
| Константы, шаблоны промптов | ELF `rodata`, `r--p` |
| Глобальные данные процесса | ELF `data/bss`, `rw-p` |
| Состояние сессии | `[heap]` или anonymous `mmap`, `rw-p` |
| Context RAM (крупные буферы) | anonymous `mmap` или `memfd`/shm, `rw-s`/`r--s` |
| Стек основного потока | `[stack]` |
| LLM-клиент, HTTP/TLS/JSON буферы | text/rodata + heap |
| Токены и ключи | **не должны** находиться в памяти Awilu (см. §5a) |
| Socket-буферы | **не VMA**: память ядра, видна в `memory.stat:sock` |

**Передача больших данных libbu ↔ агент:** `memfd_create(MFD_ALLOW_SEALING)` → `ftruncate` → `mmap`/запись → **seals** (`F_SEAL_SHRINK|GROW|WRITE|SEAL`) → `SCM_RIGHTS` по `AF_UNIX SOCK_SEQPACKET` → получатель делает `mmap(PROT_READ, MAP_SHARED)`. На сокете обязательна проверка `SO_PEERCRED`. Получатель проверяет `fstat` (тип, размер, inode), `F_GET_SEALS`, ожидаемую длину и хеш. `pipe` и обычный socket — только для мелких сообщений.

**Аудит «агент → процесс → адресное пространство»:**

```
1. pidfd_open(pid)           — устойчивый handle
2. /proc/<pid>/stat          — поле 22 starttime (защита от переиспользования PID)
3. /proc/<pid>/status        — Tgid, Pid, NStgid/NSpid, CapEff, NoNewPrivs, Seccomp
4. /proc/<pid>/cgroup        — ожидается 0::/kalam-<id>/awilu/incarnation-<id>
5. /proc/<pid>/ns/*          — inode-ы pid/user/mnt/net/cgroup
6. /proc/<pid>/exe + hash    — доказательство исполняемого кода
7. /proc/<pid>/maps, smaps_rollup — VMA (start-end, perms, pathname, Rss/Pss)
8. /proc/<pid>/fd, fdinfo    — pipe:[inode], socket:[inode]
9. kcmp(pid1, pid2, KCMP_VM) — 0 = общий mm_struct (запрещено), иначе разные
```

`maps`/`smaps` читаются одним полным чтением (иначе гонка с изменениями VMA). `kcmp` вызывать на замороженной инкарнации.

**Инварианты адресного пространства:**

1. Одна инкарнация — один `mm_struct`; потоки делят его, отдельные агенты — никогда (`kcmp(KCMP_VM) ≠ 0`).
2. Процесс запускается **в целевом cgroup до** загрузки контекста и крупных буферов (иначе страницы останутся начислены старой cgroup).
3. Крупные передаваемые буферы — `memfd` с seals и `SCM_RIGHTS`; получатель монтирует только `PROT_READ`.
4. Агент не видит VMA другого агента; доказательство — отсутствие общих file-backed shared VMA и `kcmp ≠ 0`.

---

## 5. Жизненный цикл и fencing

**Состояния:** `dormant → starting → running → sleeping → dead`, плюс `migrating`.

**Epoch:** у каждой инкарнации есть монотонный `incarnation_epoch`. Активный `qatum` привязан к нему; после fencing старый epoch не может создавать системные эффекты.

**Порядок при смерти/продаже/миграции (жёсткий, из критики Sonnet):**

1. durable bump epoch (запись в журнал до любых действий);
2. `cgroup.kill` по leaf-cgroup;
3. ждать `cgroup.events: populated=0`;
4. смена владельца/состояния;
5. новая generation при следующем запуске.

Только такой порядок гарантирует, что «старый» агент не запишет в базу после смены владельца. Внешние эффекты дополнительно защищаются idempotency-ключами, привязанными к epoch.

**Сон** — не `SIGSTOP`: checkpoint записан, процессы остановлены, leaf-cgroup пуст, systemd-юнита нет. При пробуждении capability **перепроверяются**, а не продолжаются.

**Продажа `.anima`:** quiesce → fence → отзыв qatum → смена владельца → новая generation. `.anima` — переносимый артефакт и **не действующая авторизация**.

---


### 5a. Доступ к LLM: Subur — один endpoint на Kalam, Awilu — свой

**Subur (служебные): один логический endpoint на Kalam.**

```
Subur → kalam-llm (VIP/service) → 2–3 stateless реплики Kalam-LLM → провайдеры
```

Один логический endpoint даёт: единую политику (разрешённые модели, лимиты контекста), иерархические квоты `installation → subsystem → agent → incarnation`, аудит, ротацию ключей, circuit breaker и нормализацию разных API. Отказоустойчивость — 2–3 stateless-реплики за VIP, локальный unix-socket для Subur, HA-хранилище квот и append-only аудит с локальным буфером.

**`capd` в data path не стоит**: он выдаёт и отзывает права, но каждый LLM-запрос через него не проходит.

**Awilu (пользовательские): каждый может иметь свой endpoint.**

```
Awilu ──(unix socket + lease)──▶ awilu-egress ──(провайдерский ключ)──▶ endpoint_A
                                  (другой UID, cgroup, netns)
```

- `capd` выдаёт **lease**: `lease_id`, `incarnation_id`, `endpoint_id`, `models`, `aud`, `epoch`, `not_before/expires_at`, `max_input_tokens`, `max_output_tokens`, `max_requests`, `concurrency`. Lease подписан и привязан к epoch.
- **Ключ провайдера никогда не попадает в адресное пространство Awilu.** Его держит `awilu-egress` — отдельный процесс под другим UID/cgroup и, желательно, в отдельном network namespace. Агент получает только путь к unix-socket и lease.
- **Egress default deny:** агенту разрешён `AF_UNIX`; `AF_INET/AF_INET6` закрыт; egress-прокси разрешает только заданные destination; дополнительно cgroup-BPF `connect4/connect6` и LXD ACL.

Причина жёсткости: если агент сам открывает HTTPS с bearer-токеном, **токен неизбежно окажется в его памяти** хотя бы на время запроса, а DPoP/mTLS не спасает от кода внутри того же процесса.

**Сравнение вариантов:**

| Вариант | Плюсы | Минусы | Вердикт |
|---|---|---|---|
| А. Все через общий gateway | максимум аудита, простые квоты и ротация | общий blast radius, задержка, сложнее нестандартные API | для Subur |
| Б. Awilu напрямую с токеном | минимальная задержка | токен в памяти агента, слабее квоты | только особая политика |
| **В. Гибрид** | централизованный Subur + индивидуальные маршруты Awilu | два data-plane режима, нужен общий lease-протокол | **выбран** |

**Учёт токенов и квоты (три уровня):**

1. `Kalam-LLM` / `awilu-egress` — оперативный authoritative counter: reservation до запроса (`input_estimate + max_output`), стриминговый подсчёт, немедленный `cancel` при hard limit.
2. Провайдер — сверка фактического расхода (включая cached/reasoning), не годится для мгновенного enforcement.
3. Сетевой egress — контроль аномалий (байты, соединения, destinations).

**Отказы:**

| Сценарий | Поведение |
|---|---|
| Endpoint недоступен | retry с backoff + circuit breaker; инкарнация живёт, задача помечается `blocked` |
| Lease отозван / истёк | 401/403 на прокси; агент получает `TOOL_FAIL`, эскалация в Subur |
| Квота исчерпана | hard stop до запроса; состояние `quota_exhausted`, сон/эскалация |
| Ключ скомпрометирован | ротация ключа в egress, инвалидация lease по epoch, fence инкарнации при необходимости |

**Инварианты LLM-доступа:**

1. В памяти процесса Awilu нет провайдерских ключей: проверка — отсутствие секретов в `/proc/<pid>/environ`, `maps` и heap-дампах, плюс egress default deny.
2. Любой вызов модели у Awilu идёт через lease, привязанный к `incarnation_epoch`; после fence старый lease не действует.
3. У Subur ровно один логический endpoint на Kalam; у Awilu — свой маршрут/аккаунт/модель, но не обязательно свой ключ.
4. Квоты считаются до запроса (reservation), а не после; иначе hard limit не работает.

---

## 6. Запуск процессов (критика Opus)

`.scope` в systemd создаётся **для уже работающего** процесса: между `fork/exec` и переносом в scope процесс живёт без лимитов и может успеть сделать `fork`. Поэтому:

- **основной путь** — transient **`.service`** с `ExecStart`: лимиты, credentials, namespace, seccomp и capability bounding set применяются **до** исполнения кода Awilu;
- альтернатива для высоких нагрузок — `Delegate=yes` на `kalam-<id>.slice` и `clone3(CLONE_INTO_CGROUP)` в заранее созданную leaf-cgroup; тогда systemd ниже делегированной границы ничего не трогает;
- смешивать оба пути в одной реализации нельзя.

Пустой scope systemd собирает мусором, поэтому **сон не должен иметь представления в systemd**: сон — это запись в durable-состоянии, а не живой юнит.

---

## 7. Durable и runtime

**Durable:** `kalam_id`, `anima_id`, `incarnation_id`, `generation`, lifecycle state, желаемое состояние, ссылка на `.anima`, политика ресурсов и capability, имя systemd-юнита, история переходов, checkpoint.

**В памяти:** PID/pidfd, дочерние процессы, IPC-соединения, health, счётчики, correlation ID.

**PID не durable.** После рестарта `init` сверяет durable state с фактическим состоянием systemd/cgroup и восстанавливает индекс. Истина о живых процессах — systemd, cgroup и pidfd.

---

## 8. Python → Rust: что проектировать сразу

Не меняется: `kalam_id`, `anima_id`, `incarnation_id`, `generation`, дерево cgroup, state machine, capability-модель, формат аудита, IPC, правила сна/смерти.

Проектировать сразу:
1. версионированный IPC-протокол, не зависящий от Python-классов;
2. идемпотентные команды lifecycle;
3. языконезависимый `.anima`: manifest + schema_version + hashes + runtime requirements;
4. checkpoint как данные предметной модели, а не дамп heap/event loop;
5. capability как подписанные handles или broker-mediated операции;
6. launcher как отдельный Subur с декларативным launch spec;
7. структурированный аудит с фиксированной схемой;
8. контракт завершения: exit code, signal, OOM, timeout, revoked capability, panic;
9. runtime adapter: `PythonRuntime` → `RustRuntime` без изменения оркестрации.

---

## 9. Инварианты

1. Каждый живой процесс принадлежит ровно одному leaf-cgroup одного Kalam; процесс Awilu — ровно одной `incarnation_id + generation`.
2. PID никогда не является идентичностью: идентичность — UUID + generation + cgroup.
3. Код Awilu не исполняется до применения cgroup, credentials, namespace, seccomp и capability bounding set.
4. Граница capability не слабее границы отказа: разные привилегии — разные процессы.
5. `sleeping` и `dead` требуют пустого incarnation-cgroup; `running` с пустой cgroup — отказ, а не повод «поправить» durable state.
6. Запуск — только через systemd transient `.service` **или** делегированную cgroup с `clone3(CLONE_INTO_CGROUP)`; «форкнуть и перенести» запрещено.
7. Epoch повышается durable **до** убийства процессов; идемпотентность внешних эффектов привязана к epoch.
8. В API и постоянном состоянии Libbu нет доменных слов harness: `epishu`, `subur`, `awilu`, `anima`, `incarnation`, `sleep`, `death`, `day`. Проверяется в CI.

---

## 10. Открытые вопросы

- **Группы** — позже, по образцу cgroups v2 и namespaces: как единица планирования и квот, а не как сущность владения.
- `.anima` manifest — полный список полей и версионирование (отдельный документ).
- Политика деградации по графу зависимостей при падении Subur.
- Миграция между Kalam и между ядрами: протокол с fencing и перевыпуском capability.

---

## 11. Метрики первого месяца

| Метрика | Цель |
|---|---|
| Class leakage (доменные слова в API/схемах Libbu) | 0 |
| Stale-epoch acceptance (операции старого epoch приняты) | 0 |
| Recovery correctness (нет двух активных инкарнаций, нет orphan-процессов) | 100 % |
