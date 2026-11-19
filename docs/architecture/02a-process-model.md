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
