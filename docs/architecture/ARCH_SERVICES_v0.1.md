# Архитектура сервисов Nemeq v1 (как есть по спеке)

**Дата:** 2026-11-24 · **Схема:** `nemeq_services.png/svg/dot` · **Основание:** `APISERVER v0.4`, `TEXTSTORE v0.2`, `02b` layout.

## 1. Сервисы и endpoints

| Компонент | Где | Endpoint | Назначение |
|---|---|---|---|
| **apiserver** | c-nemeq, 10.238.183.32 | `https://192.168.96.46:6443` (mTLS) | единственный control-plane API: CRUD/watch/admission/audit |
| **ocmanager UI** | abuntu .51 (nginx) | `https://192.168.96.51/` | веб-UI: epishu, сессии, права, audit, метрики; ходит только в API |
| **nqctl** | рабочая станция/хост | `~/.nq/config` → 6443 | CLI без демона: apply/get/describe/logs/watch |
| **llm-gateway** | c-nemeq | `http://10.238.183.32:8080` (internal) | read-only вызовы LLM; ключи вне Awilu |
| **exporter** | c-nemeq | `:9101/metrics` | метрики Libbu/epishu |
| **VictoriaMetrics** | gsrv .30 | `:8428` | хранение метрик |
| **Grafana** | gsrv .30 | `:3000` | дашборды |
| **OpenCloud** | abuntu .51 | `https://192.168.96.51:8092` | файлы, выгрузки |
| **Gitea** | amgm .46 (фронт 8090) | `http://192.168.96.51:8090` | спеки, манифесты, GitOps |

API-пути: `/apis/nemeq.io/v1alpha1/{epishus,sessions,grants,quotas,models}`, `/healthz`, `/openapi.json`, `/metrics`.
Watch: `GET ...?watch=1&fromRev=NNN` (410 при отставании). CAS: `resourceVersion` в теле/заголовке.

## 2. Как работает nqctl

1. Читает `~/.nq/config`: endpoint, CA, токен (или mTLS-сертификат).
2. `nqctl apply -f epishu.yaml` → YAML → JSON-операции → `PUT /apis/.../epishus/<name>` с `expectedResourceVersion` (если есть).
3. apiserver: authN → admission (RBAC/quota/policy: в v1 deny внешних эффектов) → CAS → group commit + fsync → ответ с `resourceVersion`.
4. Контроллеры (massaru/session/gateway) получают watch-событие и приводят фактическое состояние.
5. `nqctl get/describe` — list/get + события; `nqctl logs -f` — прокси через API к контроллеру/рантайму; `nqctl exec` — только после v1.
6. `nqctl store fsck|snapshot|verify` — **локальная** админ-команда (unix socket/файлы), не через сеть.

## 3. Файловые пути

```
/opt/nemeq/                              # immutable images
├── zi/<name>@<version>/                  # manifest.json, anima.toml, skills, knowledge, style, zikru-zi
└── su/<name>@<version>/                  # manifest.json, body.toml, tools

/var/nemeq/                              # mutable root
├── apiserver/                           # control-plane store
│   ├── lock                             # flock (один писатель)
│   ├── log/000001.jsonl                 # append-only журнал, hash-chain
│   ├── snap/0001000.json                # снапшот состояния
│   ├── anchor/0001000.anchor            # HMAC-якорь (rev, hash)
│   ├── state/                           # материализация (НЕ истина)
│   ├── views/index.md, audit.csv        # генерируемые представления
│   └── meta.json                        # fmt, committedOffset, rev, last_hash
├── epishu/<id>/
│   ├── instance.toml                    # zi/su refs+digests, model, kind, state
│   ├── tush.toml                        # days/incarnations
│   ├── paqidutu/{sleep,hourly,manual}.json
│   ├── zikru-zi/                        # soul memory overlay (no PII)
│   ├── zikru-su/                        # body memory (PII only here)
│   ├── view/                            # merged rootfs (zi ∪ su, ro)
│   └── Паспорт_агента.md, MEMORY*.md, Статус_сегодня.md (генерируются)
├── state/<id>/                          # atomic replace (tmp+rename)
│   ├── memory-index.json, fts.jsonl, hot.json
│   ├── day-status.json, usage.json, checkpoint.json
│   └── installation/{index,usage,reservations,pricing}.json
├── data/                                # append-only
│   ├── epishu/<id>/{atoms,wal,claims.jsonl,settlement.jsonl,traces,usage,archive}
│   ├── upstream_log/<YYYY-MM>/
│   ├── tuppu/                           # event log with epishu_id
│   └── archive/
└── secrets/                             # 0700, secretRef only (не в журнале)

/run/nemeq/epishu/<id>/paqidutu-draft    # volatile
/proc/nemeq/{qatum,agents,sessions,context,cost,kalam}   # read-only live view
```

## 4. Границы

- **Control plane** (API/store/controllers) и **data plane** (epishu, zikru, LLM-вызовы) разделены: данные не идут через apiserver.
- UI не пишет в обход API; контроллеры не пишут в файлы в обход reconcile.
- `state/` и `views/` — производные; истина — журнал.
- Секреты — только `secrets/` и `secretRef`; в журнал не попадают.
