# Рой: минимальный task board (v0.1)

**Дата:** 28.11.2026 · **Прототип:** `tools/taskboard.py` · **Связь:** SDD Nemeq §сессии/Lease, Anima 1.1.0.

## Модель
- Задача: `id, title, owner, criteria, deadline, deps, status, version, agent, lease_until, result, verifier`.
- Статусы: `queued → running → review → done|failed`; `blocked` — отдельно.
- Хранение: append-only `events.jsonl`, проекция пересчитывается при загрузке.
- Конфликт: один активный lease; повторный claim при живом lease → код 2; CAS по версии.

## Правила
1. Приёмка — **не исполнитель** (owner может).
2. Lease истекает; просроченный можно перевзять.
3. Зависимости: claim при невыполненных deps → код 2.
4. Trace: все переходы — события (`add/claim/heartbeat/complete/accept/block`).

## CLI
```bash
taskboard.py add "Собрать отчёт" --owner versta --criteria "источник+замер"
taskboard.py claim <id> luch --ttl 30
taskboard.py complete <id> luch "результат"
taskboard.py accept <id> versta [--fail]
taskboard.py list [--status done]
```

## DoD
1. 5 задач: 5 статусов, 0 потерянных.
2. Два агента не могут держать один lease.
3. Приёмка исполнителем → отказ.
4. Просроченный lease перевзят.
5. Зависимости блокируют claim.
6. После перезагрузки проекция совпадает с журналом.

## Дальше
- Интеграция с Libbu (очередь + capabilities).
- Event log → JetStream при нескольких узлах.
