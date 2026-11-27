"""Instance layout for Nemeq massaru (SDD v0.4 §4, 02b) + резолверы путей v0.6.

Две части:

1. **Раскладка внутри инстанса** (SDD v0.4 §4): `STANDARD_DIRS`, `epishu_dir`,
   `layout_paths` — контракт `epishu/<id>/{wal,atoms,...}` сохраняется полностью.
2. **Резолверы корня** (NEMEQ_HOME_LAYOUT v0.6 §5): единственный источник истины
   для путей. Ни один модуль не собирает путь сам.

Порядок приоритета (v0.6 §5):

    home:     --home       > NEMEQ_HOME     > $HOME/.nemeq
    instance: --instance   > NEMEQ_INSTANCE > "default"
    var:      --state-root > NEMEQ_VAR_ROOT > <home>/instances/<instance>/var
    store:    --store      > NEMEQ_STORE    > <var>/store

Правила: переопределения абсолютны; относительный путь — PathError (код 2);
отсутствие $HOME без явного home — отказ, не фоллбэк; имя инстанса — один
безопасный компонент пути.

Строки legacy-путей (`/var/nemeq` и др.) допустимы только в `massaru/legacy.py`.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOME_NAME = ".nemeq"
DEFAULT_INSTANCE = "default"

# Имя инстанса: один безопасный компонент (v0.6 §5.6).
INSTANCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Коды возврата (v0.6 §7.9).
EXIT_BAD_INVOCATION = 2

# Directories from SDD v0.4 §4 / 02b.
STANDARD_DIRS = (
    "daily",
    "weekly",
    "monthly",
    "paqidutu",
    "zikru-zi",
    "zikru-su",
    "wal",
    "atoms",
    "traces",
    "usage",
    "raw",
    "archive",
    "observability_log",
    "state",
    "view",
)

STANDARD_FILES = (
    "instance.toml",
    "tush.toml",
    "claims.jsonl",
    "settlement.jsonl",
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PathError(Exception):
    """Неверная конфигурация путей. Соответствует коду 2."""

    exit_code = EXIT_BAD_INVOCATION


# --------------------------------------------------------------------------
# Часть 1: раскладка инстанса (без изменений по существу — контракт SDD v0.4)
# --------------------------------------------------------------------------

def safe_epishu_id(epishu_id: str) -> str:
    """Return a path-safe epishu id or raise ValueError."""
    if not isinstance(epishu_id, str) or not _SAFE_ID.fullmatch(epishu_id):
        raise ValueError("invalid epishu_id")
    if epishu_id in {".", ".."}:
        raise ValueError("invalid epishu_id")
    return epishu_id


def epishu_root(state_root: Path) -> Path:
    return state_root / "epishu"


def epishu_dir(state_root: Path, epishu_id: str) -> Path:
    return epishu_root(state_root) / safe_epishu_id(epishu_id)


def instance_file(state_root: Path, epishu_id: str) -> Path:
    return epishu_dir(state_root, epishu_id) / "instance.toml"


def layout_paths(state_root: Path, epishu_id: str) -> dict[str, str]:
    """Return the 02b layout as path strings (no filesystem access)."""
    base = epishu_dir(state_root, epishu_id)
    payload = {"root": str(base)}
    payload.update({name: str(base / name) for name in STANDARD_DIRS})
    payload.update({name: str(base / name) for name in STANDARD_FILES})
    return payload


# --------------------------------------------------------------------------
# Часть 2: резолверы корня (NEMEQ_HOME_LAYOUT v0.6 §5)
# --------------------------------------------------------------------------

def _absolute(value: str, what: str) -> Path:
    """Переопределение обязано быть абсолютным. Относительный путь — ошибка."""
    p = Path(value)
    if not p.is_absolute():
        raise PathError(f"{what} must be an absolute path, got {value!r}")
    return p


def resolve_home(
    explicit: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Корень префикса. Приоритет: --home > NEMEQ_HOME > $HOME/.nemeq."""
    environ = os.environ if env is None else env
    if explicit is not None:
        return _absolute(str(explicit), "--home")

    from_env = environ.get("NEMEQ_HOME")
    if from_env:
        return _absolute(from_env, "NEMEQ_HOME")

    home = environ.get("HOME")
    if not home:
        raise PathError(
            "cannot resolve home: $HOME is empty and neither --home nor NEMEQ_HOME is set"
        )
    return _absolute(home, "HOME") / DEFAULT_HOME_NAME


def resolve_instance(
    explicit: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Имя инстанса. Приоритет: --instance > NEMEQ_INSTANCE > "default".

    Пустая строка из CLI/env — ошибка, а не «не задано»: иначе явный
    `--instance ""` молча подменяется дефолтом, и пользователь получает чужой
    инстанс вместо отказа.
    """
    environ = os.environ if env is None else env
    if explicit is not None:
        name = explicit
    elif "NEMEQ_INSTANCE" in environ:
        name = environ["NEMEQ_INSTANCE"]
    else:
        name = DEFAULT_INSTANCE
    if not INSTANCE_RE.fullmatch(name):
        raise PathError(
            f"invalid instance name {name!r}: expected ^[a-z0-9][a-z0-9_-]{{0,31}}$"
        )
    return name


def instance_root(home: Path, instance: str) -> Path:
    """<home>/instances/<instance>. Имя уже провалидировано в resolve_instance."""
    return home / "instances" / instance


def resolve_var(
    home: Path,
    instance: str,
    explicit: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    allow_outside_home: bool = False,
) -> Path:
    """Mutable-корень. Приоритет: --state-root > NEMEQ_VAR_ROOT > <inst>/var.

    Явный `--state-root` — это осознанное решение оператора (тесты, стенды,
    staging), поэтому он НЕ ограничивается home. Ограничение действует только
    на переменную окружения: env-переопределение легко унаследовать незаметно,
    и тогда стор молча уезжает наружу.
    """
    environ = os.environ if env is None else env

    if explicit is not None:
        return _absolute(str(explicit), "--state-root")

    default = instance_root(home, instance) / "var"
    from_env = environ.get("NEMEQ_VAR_ROOT")
    if from_env is None:
        return default

    p = _absolute(from_env, "NEMEQ_VAR_ROOT")
    if not allow_outside_home and not _is_within(p, home):
        raise PathError(
            f"var outside home requires --allow-outside-home: {p} not under {home} "
            f"(set via NEMEQ_VAR_ROOT)"
        )
    return p


def resolve_store(
    var: Path,
    explicit: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    allow_outside_home: bool = False,
    home: Path | None = None,
) -> Path:
    """Стор. Приоритет: --store > NEMEQ_STORE > <var>/store.

    Как и у var: явный `--store` законен вне home, env-переопределение — нет.
    """
    environ = os.environ if env is None else env

    if explicit is not None:
        return _absolute(str(explicit), "--store")

    default = var / "store"
    from_env = environ.get("NEMEQ_STORE")
    if from_env is None:
        return default

    p = _absolute(from_env, "NEMEQ_STORE")
    if not allow_outside_home and home is not None and not _is_within(p, home):
        raise PathError(
            f"store outside home requires --allow-outside-home: {p} not under {home} "
            f"(set via NEMEQ_STORE)"
        )
    return p


def resolve_state_root(explicit: str | None = None) -> Path:
    """Совместимость: var-корень без инстанса.

    Оставлено для кода, который ещё не переведён на resolve_all(). Дефолт —
    `$NEMEQ_HOME/instances/default/var`, а не системный путь.
    """
    home = resolve_home(None)
    instance = resolve_instance(None)
    return resolve_var(home, instance, explicit)


def lock_path(var: Path, store: Path, home: Path | None = None) -> Path:
    """Путь лока по v0.6 §6.4: <locks>/<sha256(realpath(store))>.lock.

    Полный хеш (не усечённый), отдельный подкаталог `locks/`:
      * `destroy` его не трогает — нет unlink-lockfile race;
      * `migrate` его не копирует — не входит в перечень;
      * внешний `--store` не зависит от прав родителя стора;
      * путь всегда внутри записываемого корня, поэтому попадает в
        ReadWritePaths.

    Каталог локов:

      * стор внутри `<var>` — `<var>/locks/`, как в §6.4;
      * внешний стор — `<home>/locks/`.

    Разделение обязательно из-за конфликта двух нормативных требований §6.4:
    «лок в `<var>/locks/`» и «один store — один lock, путь зависит только от
    realpath(store), не от имени инстанса». При внешнем сторе у двух
    инстансов разные `<var>`, и лок в `<var>/locks/` дал бы ДВА лока на один
    стор — ровно то, что §6.4 запрещает. Поэтому лок внешнего стора живёт в
    общем для home каталоге: имя по-прежнему зависит только от realpath(store).
    """
    digest = hashlib.sha256(os.path.realpath(store).encode("utf-8")).hexdigest()
    return _lock_base(var, store, home) / "locks" / f"{digest}.lock"


def _lock_base(var: Path, store: Path, home: Path | None) -> Path:
    """Каталог, внутри которого лежит `locks/`. Зависит от СТОРА, не от вызова.

    Если бы база бралась от вызывающего инстанса, один и тот же стор получил бы
    два лока: для владельца он внутренний (`<var>/store`), для соседа —
    внешний (`--store`). Это ровно то, что §6.4 запрещает.

    Правило:

      * стор внутри `<home>/instances/<name>/var/` — база `<home>/instances/
        <name>/var` (то есть `<var>/locks/`, как требует §6.4);
      * любой другой стор (внешний `--store`) — база `<home>`, общая для всех
        инстансов этого home.
    """
    if home is None:
        return var

    real_store = Path(os.path.realpath(store))
    real_home = Path(os.path.realpath(home))
    try:
        rel = real_store.relative_to(real_home)
    except ValueError:
        return home

    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "instances" and parts[2] == "var":
        return home / "instances" / parts[1] / "var"
    return home


def pycache_dir(var: Path) -> Path:
    return var / "pycache"


def tmp_dir(var: Path) -> Path:
    return var / "tmp"


def logs_dir(home: Path, instance: str) -> Path:
    return instance_root(home, instance) / "logs"


def run_dir(home: Path, instance: str) -> Path:
    return instance_root(home, instance) / "run"


def config_dir(home: Path) -> Path:
    return home / "config"


def lib_dir(home: Path) -> Path:
    return home / "lib"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class Resolved:
    """Итог резолвинга: пути плюс источник каждого (для `nqctl status`)."""

    home: Path
    instance: str
    var: Path
    store: Path
    lock: Path
    sources: dict[str, str]


def resolve_all(
    home: str | None = None,
    instance: str | None = None,
    state_root: str | None = None,
    store: str | None = None,
    allow_outside_home: bool = False,
    env: dict[str, str] | None = None,
) -> Resolved:
    """Единая точка резолвинга для CLI и сервисов."""
    environ = os.environ if env is None else env

    h = resolve_home(home, environ)
    inst = resolve_instance(instance, environ)
    v = resolve_var(h, inst, state_root, environ, allow_outside_home)
    s = resolve_store(v, store, environ, allow_outside_home, h)

    sources = {
        "home": "--home" if home else ("env(NEMEQ_HOME)" if environ.get("NEMEQ_HOME") else "default"),
        "instance": "--instance" if instance else ("env(NEMEQ_INSTANCE)" if environ.get("NEMEQ_INSTANCE") else "default"),
        "var": "--state-root" if state_root else ("env(NEMEQ_VAR_ROOT)" if environ.get("NEMEQ_VAR_ROOT") else "default"),
        "store": "--store" if store else ("env(NEMEQ_STORE)" if environ.get("NEMEQ_STORE") else "default"),
    }
    return Resolved(home=h, instance=inst, var=v, store=s,
                    lock=lock_path(v, s, h), sources=sources)
