# Общая модель памяти Anima ↔ Nemeq (v1)

**Статус:** предложение к SDD Nemeq v0.5/§8; реализация в Anima — runtime 0.5.0 / spec 3.2.0.

**Дата:** 28.11.2026 · **Стороны:** Anima runtime 0.5 (чужой harness) · Nemeq (собственный harness).
**Цель:** одни и те же идеи и поля памяти в двух контурах; Nemeq добавляет права/tenancy, Anima — провенанс и audit.

## 1. Единица памяти

**Атом** — факт/урок/запись с провенансом. Один формат в обоих контурах:

| Поле | Значения | Смысл |
|---|---|---|
| `entity` | строка | адресуемый ключ |
| `value` | JSON/строка | содержимое (≤200 симв.) |
| `source` | `файл:строка` / URL / команда | откуда факт |
| `source_class` | primary/secondary/computed | доверие к источнику |
| `verified_at` | ISO | когда проверено |
| `status` | fact/hypothesis/stale/retired | зрелость |
| `ttl` | дни/ISO | срок жизни |
| `confidence` | 0..1 | уверенность |
| **`memory_kind`** | `self` / `user` / `fact` | слой памяти |
| **`classification`** | `public` / `internal` / `personal` / `sealed` | класс доступа |

## 2. Слои памяти (общие имена)

| Слой | Что | ПД | Аналог |
|---|---|---|---|
| `self` | самознание: уроки, стиль, правила | **нет** | Nemeq `zikru-zi` |
| `user` | память о пользователе | **да** | Nemeq `zikru-su` |
| `fact` | нейтральные факты мира | обычно нет | Anima `atoms` |

Правила:
- `self` + `personal/sealed` — **запрещено** (код 1): уроки не должны содержать ПД;
- `user` по умолчанию `personal`;
- `fact` по умолчанию `internal`.

## 3. Классификация и доступ

| Класс | Кто видит |
|---|---|
| `public` | все |
| `internal` | агенты контура (по умолчанию) |
| `personal` | владелец зоны и явный запрос |
| `sealed` | только по ACL/явному разрешению (Nemeq: `admin_epishu` не читает sealed) |

`recall` по умолчанию: `fact`+`self`, классы `public/internal`. `personal` — только с `--include-personal`; `sealed` — только с `--include-sealed` (в Nemeq дополнительно ACL).

## 4. Наследование класса

Сводки, chunks, embeddings наследуют **максимальный** класс источника:
`sealed > personal > internal > public`. Производное не может быть «открытее» оригинала.

## 5. Удаление

- Anima: `evict` (архив) + `forget --hard` (удаление из атомов, индекса, дневника; журнал удаления остаётся с хешем).
- Nemeq: `hard-delete/TTL` как штатная операция с аудитом.

## 6. Что где реализуется

| Возможность | Anima | Nemeq |
|---|---|---|
| атом + провенанс | да (0.5) | да |
| `memory_kind` / `classification` | да (0.5) | да (SDD v0.5 §8) |
| RBAC/tenancy/sealed | нет (зона) | да |
| hard-delete | 0.5 | да |
| audit A-02/A-06/A-19/A-22 | да | берёт из Anima |
| checkpoint индекса | 0.5 | да |

## 7. Совместимость

Старые атомы без новых полей читаются как `memory_kind=fact`, `classification=internal` — миграция не требуется.
