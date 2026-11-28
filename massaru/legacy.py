"""Единственное место, где допустимы legacy-пути ОС (v0.6 §4.1).

Архитектурный тест §9.1 разрешает строки `/var/nemeq`, `/var/lib/nemeq`,
`/opt/nemeq`, `/run/nemeq` **только** в этом модуле. Всё остальное обязано
ходить через резолверы `massaru.layout`.

Модуль отвечает за:

  * таблицу преобразования «источник -> цель» (§4.1);
  * отказ при файле вне таблицы (код 7);
  * отказ при конфликте двух legacy-путей стора (код 7);
  * сопоставление элемента источника с целью миграции.

Уровень вложенности внутри `epishu/<id>/` не меняется: `wal/`, `atoms/`,
`traces/`, `state/`, `view/`, `paqidutu/`, `zikru-zi/`, `zikru-su/`, `raw/`,
`archive/`, `observability_log/` остаются внутри каждого `epishu/<id>/`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EXIT_NO_RULE = 7


class LegacyError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Legacy-корни: единственные абсолютные пути ОС, которые мы понимаем.
VAR_NEMEQ = "/var/nemeq"
VAR_LIB_NEMEQ = "/var/lib/nemeq"
OPT_NEMEQ = "/opt/nemeq"
RUN_NEMEQ = "/run/nemeq"


@dataclass(frozen=True)
class Mapping:
    """Правило преобразования одного элемента верхнего уровня."""

    source: str            # legacy-путь (или его префикс)
    target: str            # относительный путь внутри инстанса
    kind: str              # "rename" | "copy" | "skip"
    note: str = ""


# Таблица §4.1. Порядок важен: `apiserver` и `lib` претендуют на один target.
MAPPINGS: tuple[Mapping, ...] = (
    Mapping(f"{VAR_NEMEQ}/apiserver", "store", "rename",
            "стор аписервера переезжает в var/store"),
    Mapping(f"{VAR_LIB_NEMEQ}", "store", "rename",
            "альтернативный legacy-путь стора"),
    Mapping(f"{VAR_NEMEQ}/store", "store", "copy",
            "уже новая раскладка: элемент называется store"),
    Mapping(f"{VAR_NEMEQ}/epishu", "epishu", "copy"),
    Mapping(f"{VAR_NEMEQ}/state", "state", "copy"),
    Mapping(f"{VAR_NEMEQ}/data", "data", "copy"),
    Mapping(f"{OPT_NEMEQ}", "lib", "copy", "immutable-образы"),
    Mapping(f"{RUN_NEMEQ}", "run", "skip", "runtime, пересоздаётся"),
)

# Цели, на которые претендуют два источника: конфликт -> код 7.
AMBIGUOUS_TARGETS: dict[str, tuple[str, ...]] = {
    "store": (f"{VAR_NEMEQ}/apiserver", f"{VAR_LIB_NEMEQ}"),
}


def _mappings_by_leaf() -> dict[str, Mapping]:
    """Индекс таблицы по имени элемента источника.

    Имена не совпадают с последним компонентом legacy-пути: `/var/lib/nemeq`
    оканчивается на `nemeq`, а в дереве источника лежит как `lib`. Поэтому
    источник и элемент сопоставляются явным алиасом.
    """
    explicit = {
        "apiserver": f"{VAR_NEMEQ}/apiserver",
        "lib": VAR_LIB_NEMEQ,
        "store": f"{VAR_NEMEQ}/store",
        "epishu": f"{VAR_NEMEQ}/epishu",
        "state": f"{VAR_NEMEQ}/state",
        "data": f"{VAR_NEMEQ}/data",
    }
    by_source = {m.source: m for m in MAPPINGS}
    out: dict[str, Mapping] = {}
    for name, source in explicit.items():
        m = by_source.get(source)
        if m is not None:
            out[name] = m
    return out


def find_mapping(path: str) -> Mapping | None:
    """Найти правило для пути. Точное совпадение или вложенность."""
    clean = path.rstrip("/")
    for m in MAPPINGS:
        src = m.source.rstrip("/")
        if clean == src or clean.startswith(src + "/"):
            return m
    return None


def map_entry(path: str) -> str:
    """Цель для legacy-пути. Нет правила -> LegacyError(7)."""
    m = find_mapping(path)
    if m is None:
        raise LegacyError(
            EXIT_NO_RULE,
            f"no transformation rule for {path!r} (§4.1)",
        )
    clean = path.rstrip("/")
    src = m.source.rstrip("/")
    tail = clean[len(src):].lstrip("/")
    return f"{m.target}/{tail}" if tail else m.target


def scan_source(source_var: Path) -> dict:
    """Проверить элементы верхнего уровня источника до копирования.

    Возвращает план: список (элемент, цель, тип). Отказ — до любых записей.
    """
    if not source_var.is_dir():
        raise LegacyError(EXIT_NO_RULE, f"source is not a directory: {source_var}")

    plan: list[tuple[str, str, str]] = []
    unknown: list[str] = []
    seen_targets: dict[str, str] = {}

    # Внутри источника элементы адресуются относительным именем:
    # apiserver, epishu, state, data, lib (альтернативный стор).
    # Таблица §4.1 задана абсолютными legacy-путями, поэтому сопоставляем
    # по имени элемента, а не по полному пути.
    by_leaf = _mappings_by_leaf()

    for entry in sorted(source_var.iterdir()):
        name = entry.name
        m = by_leaf.get(name)

        if m is None:
            # Элементы, не описанные в таблице, — отказ с именем файла.
            if name in {"tmp", "locks", "pycache"}:
                continue  # внутренние, не переносятся
            unknown.append(name)
            continue

        if m.target in seen_targets and m.target != "skip":
            raise LegacyError(
                EXIT_NO_RULE,
                f"conflict on target {m.target!r}: "
                f"{seen_targets[m.target]!r} and {name!r} (§4.1)",
            )
        seen_targets[m.target] = name
        plan.append((name, m.target, m.kind))

    if unknown:
        raise LegacyError(
            EXIT_NO_RULE,
            "no transformation rule for: " + ", ".join(sorted(unknown)[:5]),
        )
    return {"plan": plan, "targets": seen_targets}


def detect_store_conflict(source_var: Path) -> str | None:
    """Конфликт двух legacy-путей стора (§4.1 требует отказа).

    Если в источнике есть и `apiserver/`, и `lib/`-подобный стор — непонятно,
    что считать стором. Здесь проверяем оба имени в одном дереве.
    """
    apiserver = source_var / "apiserver"
    lib_store = source_var / "lib"
    if apiserver.is_dir() and lib_store.is_dir():
        return (
            f"both {VAR_NEMEQ}/apiserver and {VAR_LIB_NEMEQ} present: "
            "cannot choose the store"
        )
    return None


def assert_no_conflict(source_var: Path) -> None:
    """Отказ с кодом 7 при неоднозначном сторе."""
    problem = detect_store_conflict(source_var)
    if problem:
        raise LegacyError(EXIT_NO_RULE, problem)


def is_legacy_path(path: str) -> bool:
    for root in (VAR_NEMEQ, VAR_LIB_NEMEQ, OPT_NEMEQ, RUN_NEMEQ):
        if path == root or path.startswith(root + "/"):
            return True
    return False


def legacy_roots() -> tuple[str, ...]:
    """Все известные legacy-корни — для архитектурного теста и документации."""
    return (VAR_NEMEQ, VAR_LIB_NEMEQ, OPT_NEMEQ, RUN_NEMEQ)


def contains_absolute_path(value: str) -> bool:
    """Используется fsck-проверкой (§4.2): абсолютные пути внутри данных запрещены."""
    v = (value or "").strip()
    return v.startswith("/")
