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


## 6. Условия freeze/merge (Opus/Grok, 23.11)

1. **Read-only — в механизме:** у Awilu нет write-credentials и egress к мутирующим API; deny-by-default + audit; тест «ни один путь не делает необратимый внешний вызов».
2. **Память — эффект:** записи идемпотентны (`request_id + agent_id`), версионированы, hard-delete + TTL; last-write-wins; outbox не нужен.
3. **PII-гейт:** v1 **не хранит PII/секреты** во входах, памяти, логах; redaction, data inventory; disk encryption провайдера. Если PII нужен — crypto/DSAR нельзя переносить в v2.
4. **Prompt injection/exfiltration:** нет внешнего URL-fetch, egress allowlist, ответы не содержат секретов.
5. **Метрики:** без промптов, памяти, PII.
6. **Шов v2:** effect port/outbox — только контракт, без реализации.
7. **Приёмка:** тест на отсутствие необратимых внешних вызовов; read-only enforce; PII scan.

## 7. Следствие

v1 — «стеклянный» экспертный совет: только чтение, без PII, без внешних эффектов. Всё, что требует crypto/DSAR/outbox, — v2.


## 8. Условия Луча (23.11)

1. **v1 SDD без sink/outbox/restore** — целиком, без полуреализаций; v2-главы — в feature list с замороженным rc11-дизайном как baseline.
2. **Инварианты v1:** применимые к read-only зафиксировать; остальные — N/A с причиной.
3. **Read gate, RBAC, consent/tombstone, classification, audit — в v1**; sealed-чтение — только root, admin_epishu — metadata.
4. **Full DSAR — v2**, но базовый tombstone/retention по consent — в v1.
5. **Тесты v1:** property-based RBAC, read gate, consent-каскад, classification-canary; sink/restore-тесты — v2.
