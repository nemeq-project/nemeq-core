"""Дополнение migrate: проверка WAL (§7.8) и приоритет кодов (§7.9).

До этого шага `ExitCode.DIRTY_WAL` существовал как константа, но проверки не
было: миграция грязного стора проходила молча. §7.8 требует обратного.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .migrate import ExitCode, MigrateError, MigratePlan

# Приоритет при нескольких ошибках (§7.9): чем раньше в списке, тем важнее.
# Сначала «можно ли вообще начинать», потом совместимость, потом данные.
CODE_PRIORITY: tuple[ExitCode, ...] = (
    ExitCode.BAD_INVOCATION,
    ExitCode.LOCK_BUSY,
    ExitCode.UNSUPPORTED_FS,
    ExitCode.NO_RULE,
    ExitCode.DIRTY_WAL,
    ExitCode.IDENTITY_MISMATCH,
    ExitCode.VERSION_SKEW,
    ExitCode.FOREIGN_UID,
    ExitCode.IO_ERROR,
    ExitCode.SUPPLY_HASH,
    ExitCode.DATA_DIGEST,
    ExitCode.HEALTH_FAILED,
)


def resolve_priority(codes: list[ExitCode]) -> ExitCode | None:
    """Вернуть наиболее приоритетный код из набора (или None, если пусто)."""
    if not codes:
        return None
    for code in CODE_PRIORITY:
        if code in codes:
            return code
    return codes[0]


def wal_tail_state(source_var: Path) -> dict:
    """Проверить, остались ли в источнике незавершённые транзакции.

    Признак грязного WAL — последняя строка сегмента не завершена переводом
    строки: писатель упал между записью и commit. Такой хвост нельзя молча
    копировать — он даст полу-стор, который fsck примет за валидный.
    """
    store = source_var / "store"
    log_dir = store / "log"
    if not log_dir.is_dir():
        return {"dirty": False, "files": [], "bytes": 0}

    dirty: list[str] = []
    total = 0
    for seg in sorted(log_dir.glob("*.jsonl")):
        try:
            size = seg.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue
        total += size
        try:
            with open(seg, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                last = fh.read(1)
        except OSError:
            continue
        if last != b"\n":
            dirty.append(seg.name)

    return {"dirty": bool(dirty), "files": dirty, "bytes": total}


def preflight_wal(
    plan: MigratePlan,
    *,
    recover: bool = False,
) -> dict:
    """Отказать при грязном WAL, если не запрошено восстановление (§7.8).

    Возвращает описание хвоста; при `recover=True` не отказывает, а помечает,
    что реплей нужен.
    """
    state = wal_tail_state(plan.source_var)
    if state["dirty"] and not recover:
        raise MigrateError(
            ExitCode.DIRTY_WAL,
            "dirty WAL tail (unfinished transaction) in "
            + ", ".join(state["files"][:3])
            + "; pass --recover to replay it into staging",
        )
    return state


def replay_wal_into(staging_var: Path, source_var: Path) -> int:
    """Скопировать хвост WAL в staging и обрезать до последней целой строки.

    Возвращает число отброшенных байт. Источник НЕ меняется: §7.8 требует
    «источник не изменён», поэтому работаем только с копией.
    """
    store_src = source_var / "store" / "log"
    store_dst = staging_var / "store" / "log"
    if not store_src.is_dir():
        return 0

    store_dst.mkdir(parents=True, exist_ok=True)
    dropped = 0
    for seg in sorted(store_src.glob("*.jsonl")):
        target = store_dst / seg.name
        if not target.is_file():
            continue
        data = target.read_bytes()
        if not data or data.endswith(b"\n"):
            continue
        # Обрезка до последней целой строки: всё после последнего \n — хвост.
        cut = data.rfind(b"\n")
        if cut < 0:
            target.write_bytes(b"")
            dropped += len(data)
        else:
            dropped += len(data) - (cut + 1)
            target.write_bytes(data[: cut + 1])
    return dropped
