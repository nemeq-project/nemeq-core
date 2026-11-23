# Nemeq v1 — Scope (read-only expert council)

**Дата:** 2026-11-23 · **Решение:** вариант 2 (сокращение v1) · **Статус:** канон v1.

## 1. Что входит в v1

- **3 Awilu:** Октавия (Opus), Лира (Sonnet), Ада (GPT) — экспертный совет.
- **zi/su образы** с digest, инстансы `epishu` (`zi ∪ su`), сессии (одна активная + очередь).
- **Память:** zikru-zi/zikru-su, WAL/atoms/index, claims, traces; read-after-sync.
- **LLM:** только **read-only** вызовы через `llm-gateway`/`awilu-egress`; lease/квоты; ключи вне Awilu.
- **RBAC:** admin (global root), admin_epishu, ops, viewer; группы epishu+users.
- **Метрики:** токены cached/uncached/output/reasoning, стоимость, квоты; VM/Grafana.
- **UI:** каталог epishu, сессии, контекст/память, метрики (control plane) — по мере готовности.

## 2. Что НЕ входит в v1 (v2 / feature list)

- **Внешние необратимые эффекты** (email, платежи, коммиты, публикации, webhooks) — N-sink.
- **Exactly-once / outbox / restore-протокол** для внешних эффектов (addenda §13–20 — v2).
- **DSAR** (автоматизация; ручное удаление — F-11), **crypto at rest** (F-01/F-02).
- **Cross-epishu exchange** (F-03), миграция Kalam (F-04), marketplace (F-07).

## 3. Ключевое правило

LLM-вызов считается **read-only наблюдательным** действием: он не меняет внешний мир, только читает ответ. Повтор допустим (стоимость), но дедуп по `request_id`/lease — для экономии. Внешние side effects в v1 запрещены.

## 4. Приёмка v1

1. 3 Awilu запущены на `c-nemeq` (.46), сессии/сон/пробуждение.
2. Челлендж → 3 структурированных review; координатор детерминированный; dissent.
3. Память: WAL, read-after-sync, zikru-su по пользователю; sealed ACL.
4. Метрики live в VM; дашборды.
5. Нет N-sink/DSAR/crypto в v1; ключи вне Awilu; epoch fencing.
6. Тесты: T-01…T-34 (SDD) + RB/RS v1 subset; мутанты.

## 5. Почему так

Протокол exactly-once/restore/DSAR требует распределённой формальной спецификации (TLA+/Jepsen), а экспертный совет её не требует. Сокращение v1 снимает стопперы и позволяет запустить совет; протокол эффектов — отдельной линией v2.
