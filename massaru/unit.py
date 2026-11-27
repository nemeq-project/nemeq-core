"""Генерация systemd-юнита (NEMEQ_HOME_LAYOUT v0.6 §6.5).

Требования, каждое из которых проверяется мутантом:

  * **user-unit** по умолчанию (`~/.config/systemd/user/`), системный — только
    для уровня (в); установка без root обязана работать;
  * **`%h` запрещён**: в системном юните он даёт HOME менеджера (root), а не
    `User=` — это была реальная ошибка в v0.4;
  * **абсолютные пути** подставляет генератор, а не systemd-подстановка;
  * **ReadWritePaths сужены** до `var/`, `logs/`, `run/` инстанса — весь префикс
    открывать нельзя, иначе `lib/` становится записываемым и иммутабельность
    (§4.3) держится только на честном слове;
  * **ReadOnlyPaths=lib** — best-effort: применяется только при доступных
    unprivileged user namespaces. Спецификация разрешает обещать не больше.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .layout import Resolved, instance_root, lib_dir, logs_dir, run_dir

EXIT_BAD_INVOCATION = 2

UNIT_DIR_USER = ".config/systemd/user"
UNIT_DIR_SYSTEM = "/etc/systemd/system"

# Подстроки, запрещённые в юните: подстановки systemd, которые ломают
# предсказуемость путей.
FORBIDDEN_SPECIFIERS = ("%h", "%u", "%U", "%s", "%S", "%t")


class UnitError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class UnitSpec:
    name: str
    content: str
    path: Path
    system: bool

    def as_dict(self) -> dict:
        return {"name": self.name, "path": str(self.path), "system": self.system}


def unit_name(instance: str) -> str:
    """Имя юнита обязано включать инстанс, иначе два инстанса конфликтуют."""
    return f"nemeq-{instance}.service"


def build_unit(
    resolved: Resolved,
    *,
    system: bool = False,
    user: str | None = None,
) -> UnitSpec:
    """Собрать юнит с абсолютными путями и узкими ReadWritePaths."""
    inst = resolved.instance
    var = resolved.var
    logs = logs_dir(resolved.home, inst)
    run = run_dir(resolved.home, inst)
    lib = lib_dir(resolved.home)
    nqctl = resolved.home / "bin" / "nqctl"

    lines = [
        "[Unit]",
        f"Description=Nemeq instance '{inst}'",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"Environment=NEMEQ_HOME={resolved.home}",
        f"Environment=NEMEQ_INSTANCE={inst}",
        "Environment=PYTHONDONTWRITEBYTECODE=1",
        f"ExecStart={nqctl} serve",
        "Restart=on-failure",
        "RestartSec=3",
        "KillMode=mixed",
        "TimeoutStopSec=20",
        "",
        "# Best-effort песочница: применяется при доступных userns (§6.5).",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        f"ReadWritePaths={var}",
        f"ReadWritePaths={logs}",
        f"ReadWritePaths={run}",
        f"ReadOnlyPaths={lib}",
        "",
        "[Install]",
        "WantedBy=default.target" if not system else "WantedBy=multi-user.target",
        "",
    ]
    content = "\n".join(lines)
    _reject_specifiers(content)

    if system:
        path = Path(UNIT_DIR_SYSTEM) / unit_name(inst)
    else:
        home_dir = Path(user) if user else Path(os.environ.get("HOME", "~")).expanduser()
        path = home_dir / UNIT_DIR_USER / unit_name(inst)

    return UnitSpec(name=unit_name(inst), content=content, path=path, system=system)


def _reject_specifiers(content: str) -> None:
    """`%h` и родственные подстановки запрещены (§6.5)."""
    for spec in FORBIDDEN_SPECIFIERS:
        if spec in content:
            raise UnitError(
                EXIT_BAD_INVOCATION,
                f"systemd specifier {spec} is forbidden in unit (use absolute paths)",
            )


def write_unit(spec: UnitSpec) -> Path:
    """Записать юнит. Каталог создаётся с 0700, файл — 0644."""
    spec.path.parent.mkdir(parents=True, exist_ok=True)
    if not spec.system:
        os.chmod(spec.path.parent, 0o700)
    spec.path.write_text(spec.content, encoding="utf-8")
    os.chmod(spec.path, 0o644)
    return spec.path


def verify_unit(spec: UnitSpec) -> dict:
    """Проверить инварианты готового юнита (используется в тестах и doctor)."""
    c = spec.content
    problems: list[str] = []

    for spec_ch in FORBIDDEN_SPECIFIERS:
        if spec_ch in c:
            problems.append(f"forbidden specifier {spec_ch}")

    if "ExecStart=/" not in c:
        problems.append("ExecStart must be an absolute path")

    for chunk in c.splitlines():
        if chunk.startswith("Environment=NEMEQ_HOME="):
            val = chunk.split("=", 2)[2]
            if not val.startswith("/"):
                problems.append("NEMEQ_HOME must be absolute")
        if chunk.startswith("ReadWritePaths="):
            val = chunk.split("=", 1)[1]
            if not val.startswith("/"):
                problems.append(f"ReadWritePaths must be absolute: {val}")

    # Весь префикс в ReadWritePaths — ошибка: открывает lib/ на запись.
    for chunk in c.splitlines():
        if chunk.startswith("ReadWritePaths=") and chunk.endswith("/lib"):
            problems.append("lib must not be writable")

    if "ReadOnlyPaths" not in c:
        problems.append("ReadOnlyPaths for lib is required")

    return {"ok": not problems, "problems": problems, "path": str(spec.path)}


def run_cli_unit(
    *,
    home: str | None = None,
    instance: str | None = None,
    state_root: str | None = None,
    store: str | None = None,
    system: bool = False,
    user: str | None = None,
    write: bool = True,
) -> tuple[int, dict]:
    """Слой для `nqctl install-unit`."""
    from .layout import PathError, resolve_all

    try:
        resolved = resolve_all(home=home, instance=instance, state_root=state_root,
                               store=store, allow_outside_home=True)
    except PathError as exc:
        return EXIT_BAD_INVOCATION, {"title": "Invalid", "detail": str(exc)}

    try:
        spec = build_unit(resolved, system=system, user=user)
        check = verify_unit(spec)
        if not check["ok"]:
            return EXIT_BAD_INVOCATION, {
                "title": "UnitInvalid", "problems": check["problems"],
            }
        if write:
            write_unit(spec)
        return 0, {**spec.as_dict(), "content": spec.content, "verify": check}
    except UnitError as exc:
        return exc.code, {"title": "UnitFailed", "status": exc.code, "detail": exc.message}
