"""Канонический digest стора (NEMEQ_HOME_LAYOUT v0.6 §7.3).

Назначение: доказать, что данные источника и цели миграции идентичны, включая
содержимое, а не только счётчики. В v0.2/v0.3 формула считала `atom_count` без
хешей содержимого, из-за чего заявленный критерий «подмена байта меняет digest»
из формулы не следовал.

Каноническое кодирование (длины — в БАЙТАХ, сортировка — по байтам):

    для каждого relpath по возрастанию fsencode(relpath):
        u64be(len(fsencode(relpath))) || fsencode(relpath) || u8(kind)
        kind=0 (file):    u64be(size) || sha256(content) || u32be(mode & 0o7777)
        kind=1 (symlink): u64be(len(fsencode(target))) || fsencode(target) || u32be(mode & 0o7777)
        kind=2 (dir):     u32be(mode & 0o7777)

    store_digest = "sha256:" + sha256(конкатенация)

Корень `<var>` включается в набор (kind=2, пустой relpath), иначе пустой стор и
стор с одним пустым каталогом неразличимы.

Исключаются: `tmp/` (staging, изменчив) и `locks/` (эфемерные лок-файлы).
Запрещены: hard links (st_nlink > 1) и особые файлы (fifo/socket/device) —
код 7, потому что их digest зависит от порядка обхода или не определён.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

# Коды возврата (v0.6 §7.9).
EXIT_NO_RULE = 7
EXIT_BAD_INVOCATION = 2

# Каталоги, исключённые из digest.
EXCLUDED_DIRS = ("tmp", "locks")


class DigestError(Exception):
    """Стор содержит то, что нельзя однозначно хешировать (код 7)."""

    exit_code = EXIT_NO_RULE


def _u64be(value: int) -> bytes:
    return value.to_bytes(8, "big")


def _u32be(value: int) -> bytes:
    return value.to_bytes(4, "big")


@dataclass
class DigestResult:
    """Результат: digest плюс то, что нельзя проверить молча."""

    digest: str
    files: int
    dirs: int
    symlinks: int
    empty_dirs: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "digest": self.digest,
            "files": self.files,
            "dirs": self.dirs,
            "symlinks": self.symlinks,
            "emptyDirs": self.empty_dirs,
            "skipped": self.skipped,
        }


def _entry_bytes(relpath: bytes, kind: int, body: bytes) -> bytes:
    return _u64be(len(relpath)) + relpath + bytes([kind]) + body


def _walk(root: Path) -> list[tuple[bytes, int, bytes, str]]:
    """Собрать записи: (relpath_bytes, kind, body, relpath_str).

    Порядок — по возрастанию байтов relpath, чтобы digest не зависел от порядка
    обхода ФС (критерий M-7.3 устойчивость к перестановке).
    """
    entries: list[tuple[bytes, int, bytes, str]] = []
    empty_dirs: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        here = Path(dirpath)

        # Исключаемые каталоги не обходятся вовсе.
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS
                       and not (here == root and d in EXCLUDED_DIRS)]

        for name in list(dirnames) + list(filenames):
            full = here / name
            rel = full.relative_to(root)
            relb = os.fsencode(str(rel))
            rels = str(rel)

            try:
                st = os.lstat(full)
            except OSError as exc:
                raise DigestError(f"cannot stat {rel}: {exc}") from exc

            mode = stat.S_IMODE(st.st_mode)

            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(full)
                tb = os.fsencode(target)
                body = _u64be(len(tb)) + tb + _u32be(mode)
                entries.append((relb, 1, body, rels))
                continue

            if stat.S_ISDIR(st.st_mode):
                body = _u32be(mode)
                entries.append((relb, 2, body, rels))
                # Пустой каталог фиксируется ещё и в отчёте: иначе потеря
                # epishu/<id>/wal/ при миграции не была бы видна человеку.
                if not any(True for _ in os.scandir(full)):
                    empty_dirs.append(rels)
                continue

            if not stat.S_ISREG(st.st_mode):
                raise DigestError(
                    f"unsupported file type (fifo/socket/device): {rel}"
                )

            if st.st_nlink > 1:
                raise DigestError(
                    f"hard link not supported: {rel} (st_nlink={st.st_nlink})"
                )

            dig = hashlib.sha256()
            try:
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        dig.update(chunk)
            except OSError as exc:
                raise DigestError(f"cannot read {rel}: {exc}") from exc

            body = _u64be(st.st_size) + dig.digest() + _u32be(mode)
            entries.append((relb, 0, body, rels))

    entries.sort(key=lambda e: e[0])
    return entries, empty_dirs


def store_digest(
    root: Path,
    include_root: bool = True,
) -> DigestResult:
    """Посчитать канонический digest дерева `<var>`.

    `include_root` добавляет запись kind=2 с пустым relpath — режим самой
    корневой директории входит в digest.
    """
    root = Path(root)
    if not root.is_dir():
        raise DigestError(f"not a directory: {root}")

    entries, empty_dirs = _walk(root)

    h = hashlib.sha256()
    if include_root:
        root_mode = stat.S_IMODE(os.lstat(root).st_mode)
        h.update(_entry_bytes(b"", 2, _u32be(root_mode)))

    files = dirs = symlinks = 0
    for relb, kind, body, _rels in entries:
        h.update(_entry_bytes(relb, kind, body))
        if kind == 0:
            files += 1
        elif kind == 1:
            symlinks += 1
        else:
            dirs += 1

    return DigestResult(
        digest="sha256:" + h.hexdigest(),
        files=files,
        dirs=dirs,
        symlinks=symlinks,
        empty_dirs=sorted(empty_dirs),
    )


def content_digest(root: Path) -> str:
    """Digest только содержимого: пути, типы, данные, target симлинков.

    Режимы и владельцы НЕ входят — используется для сравнения источника и
    staging ДО нормализации прав (v0.6 §7.4): источник с правами 0644 и цель
    0600 обязаны совпасть по содержимому.
    """
    root = Path(root)
    if not root.is_dir():
        raise DigestError(f"not a directory: {root}")

    entries, _ = _walk(root)
    h = hashlib.sha256()
    for relb, kind, body, _ in entries:
        if kind == 0:
            # отбрасываем u32be(mode) в хвосте
            core = body[:-4]
        elif kind == 1:
            core = body[:-4]
        else:
            core = b""  # каталог без режима
        h.update(_u64be(len(relb)) + relb + bytes([kind]) + core)
    return "sha256:" + h.hexdigest()


def metadata_digest(root: Path) -> str:
    """Digest метаданных: (relpath, mode, uid, gid).

    Сравнивается не с источником, а с ожидаемой таблицей (v0.6 §7.4): цель
    обязана иметь нормализованные права, а не права источника.
    """
    root = Path(root)
    if not root.is_dir():
        raise DigestError(f"not a directory: {root}")

    entries, _ = _walk(root)
    rows: list[bytes] = []

    root_st = os.lstat(root)
    rows.append(b"\x00" + _u32be(stat.S_IMODE(root_st.st_mode))
                + _u32be(root_st.st_uid) + _u32be(root_st.st_gid))

    for relb, kind, body, rels in entries:
        st = os.lstat(root / rels)
        mode = stat.S_IMODE(st.st_mode)
        rows.append(_u64be(len(relb)) + relb + bytes([kind])
                    + _u32be(mode) + _u32be(st.st_uid) + _u32be(st.st_gid))

    rows.sort()
    return "sha256:" + hashlib.sha256(b"".join(rows)).hexdigest()
