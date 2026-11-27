"""Архитектурный тест: ни одного пути ОС в коде (v0.6 §9.1).

В v0.1 критерий был вакуумным: `grep` с `|` без `-E` искал литерал и не находил
ничего. Здесь четыре независимых механизма, и каждый способен провалиться:

  1. текстовый поиск по ВСЕМ файлам (allow-list исключений, а не deny-list
     расширений — deny-list всегда неполон);
  2. обход `.gitignore`: файл, спрятанный от поиска, не должен проходить;
  3. AST-проверка конкатенации `Path("/var") / "nemeq"` — регулярка её не видит;
  4. содержимое wheel-архивов, включая симлинки (rg не смотрит в deflate).
"""
from __future__ import annotations

import ast
import os
import re
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Пути ОС, которых не должно быть в коде ни в каком виде.
OS_PATH_PATTERN = re.compile(
    r"/(?:var|var/lib|opt|usr/local|etc|srv|tmp)/nemeq" r"|\.nq\b"
)
SYSTEMD_SPECIFIER = re.compile(r"%[tSCEL]/nemeq")

# Единственное место, где legacy-пути разрешены (и только для миграции).
# legacy.py — единственный модуль миграции, где пути ОС нужны по смыслу.
# crit_lint.py содержит пути внутри строк-фикстур собственного selftest.
ALLOWED_FILES = {
    "massaru/legacy.py",
    "tests/p0/test_arch_paths.py",
    "tools/crit_lint.py",
    "API_SCHEMA.md",
}

# Каталоги, которые не сканируются: документация описывает пути по смыслу,
# фикстуры их специально содержат.
SKIP_DIRS = {".git", "__pycache__", "docs", "tests/fixtures", "node_modules",
             ".pytest_cache", "run", "tests/p0"}

TEXT_SUFFIXES = {
    ".py", ".rs", ".sh", ".toml", ".service", ".yml", ".yaml",
    ".j2", ".tmpl", ".json", ".cfg", ".ini", ".conf", ".socket", ".timer",
}


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel_dir = str(here.relative_to(root))
        dirnames[:] = [
            d for d in dirnames
            if not any(f"{rel_dir}/{d}".lstrip("./").startswith(s) or d == s
                       for s in SKIP_DIRS)
        ]
        for name in filenames:
            path = here / name
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                continue
            if rel in ALLOWED_FILES:
                continue
            if any(rel.startswith(s + "/") or rel == s for s in SKIP_DIRS):
                continue
            yield path, rel


def scan_text(root: Path) -> list[str]:
    """Все текстовые файлы. Файлы без расширения тоже проверяются: bin/шимы."""
    hits: list[str] = []
    for path, rel in _iter_files(root):
        if path.suffix and path.suffix not in TEXT_SUFFIXES:
            if path.suffix == ".whl":
                continue
            if not os.access(path, os.R_OK):
                continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if OS_PATH_PATTERN.search(line) and "legacy" not in line:
                hits.append(f"{rel}:{lineno}: {line.strip()[:80]}")
            elif SYSTEMD_SPECIFIER.search(line):
                hits.append(f"{rel}:{lineno}: systemd specifier: {line.strip()[:60]}")
    return hits


def scan_ast(root: Path) -> list[str]:
    """AST: ловит `Path("/var") / "nemeq"` и `os.path.join("/var", "nemeq")`."""
    hits: list[str] = []
    forbidden_roots = {"/var", "/opt", "/etc", "/run", "/srv", "/usr/local", "/var/lib"}

    for path in root.rglob("*.py"):
        rel = str(path.relative_to(root))
        if rel in ALLOWED_FILES:
            continue
        if any(s in rel for s in ("/docs/", "/tests/fixtures/", "tests/p0/")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in forbidden_roots:
                    hits.append(f"{rel}:{node.lineno}: raw literal {node.value!r}")
    return hits


def scan_gitignore(root: Path) -> list[str]:
    """Файл, спрятанный в .gitignore, не должен скрывать нарушения."""
    gi = root / ".gitignore"
    if not gi.is_file():
        return []
    hits = []
    for lineno, line in enumerate(gi.read_text(encoding="utf-8").splitlines(), 1):
        if OS_PATH_PATTERN.search(line):
            hits.append(f".gitignore:{lineno}: {line.strip()}")
    return hits


def scan_wheels(root: Path) -> list[str]:
    """Содержимое wheel-архивов и их симлинки."""
    hits: list[str] = []
    for whl in root.rglob("*.whl"):
        try:
            with zipfile.ZipFile(whl) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if info.create_system == 3 and (info.external_attr >> 16) & 0o170000 == 0o120000:
                        target = zf.read(info).decode("utf-8", "replace")
                        if target.startswith("/") or ".." in target:
                            hits.append(f"{whl.name}:{info.filename} -> {target} (symlink out)")
                        continue
                    try:
                        text = zf.read(info).decode("utf-8", "strict")
                    except (UnicodeDecodeError, RuntimeError):
                        continue
                    for line in text.splitlines():
                        if OS_PATH_PATTERN.search(line):
                            hits.append(f"{whl.name}:{info.filename}: {line.strip()[:60]}")
                            break
        except (zipfile.BadZipFile, OSError):
            continue
    return hits


class TestNoOsPaths:
    def test_no_os_paths_in_sources(self):
        """M-9.1-a: путь ОС в шаблоне/скрипте без расширения → тест падает."""
        hits = scan_text(REPO)
        assert not hits, "найдены пути ОС:\n" + "\n".join(hits[:10])

    def test_ast_no_concatenated_paths(self):
        """M-9.1-b/g: `Path("/var") / "nemeq"` → AST-тест падает."""
        hits = scan_ast(REPO)
        assert not hits, "найдена конкатенация пути:\n" + "\n".join(hits[:10])

    def test_gitignore_not_hiding(self):
        """M-9.1-c: путь в .gitignore → тест падает."""
        hits = scan_gitignore(REPO)
        assert not hits, "путь ОС спрятан в .gitignore:\n" + "\n".join(hits)

    def test_wheels_clean(self):
        """M-9.1-d/f: путь или симлинк наружу в колесе → тест падает."""
        hits = scan_wheels(REPO)
        assert not hits, "проблемы в wheel:\n" + "\n".join(hits[:10])

    def test_unit_template_no_specifier(self):
        """M-9.1-e: `%t/nemeq` в unit-шаблоне → тест падает."""
        hits = [h for h in scan_text(REPO) if "systemd specifier" in h]
        assert not hits, "\n".join(hits[:5])


class TestSelfCheck:
    """Самопроверка: тест обязан падать, если нарушение внести.

    Без этого критерий §9.1 недействителен — именно так он был вакуумным в v0.1.
    """

    def test_detects_injected_violation(self, tmp_path):
        # M-9.1-a: создаём файл с путём ОС и убеждаемся, что сканер его видит
        bad = tmp_path / "service.j2"
        bad.write_text("ExecStart=/var/nemeq/bin/nqctl\n", encoding="utf-8")
        assert scan_text(tmp_path), "сканер не увидел путь в .j2"

    def test_detects_extensionless_script(self, tmp_path):
        bad = tmp_path / "shim"
        bad.write_text("#!/bin/sh\nexport X=/var/nemeq\n", encoding="utf-8")
        assert scan_text(tmp_path), "сканер не увидел путь в файле без расширения"

    def test_detects_ast_concatenation(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text('from pathlib import Path\nP = Path("/var") / "nemeq"\n',
                       encoding="utf-8")
        assert scan_ast(tmp_path), "AST не увидел конкатенацию"

    def test_does_not_pretend_regular_expression_catches_it(self, tmp_path):
        """Регулярка НЕ ловит конкатенацию — это и есть причина AST-проверки."""
        src = tmp_path / "mod.py"
        src.write_text('from pathlib import Path\nP = Path("/var") / "nemeq"\n',
                       encoding="utf-8")
        assert scan_text(tmp_path) == [], "регулярка неожиданно поймала конкатенацию"
        assert scan_ast(tmp_path) != [], "AST обязан поймать"

    def test_detects_wheel_symlink_escape(self, tmp_path):
        import zipfile

        whl = tmp_path / "bad-1.0-py3-none-any.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            info = zipfile.ZipInfo("evil")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
        assert scan_wheels(tmp_path), "сканер колеса не увидел симлинк наружу"

    def test_detects_wheel_text_violation(self, tmp_path):
        import zipfile

        whl = tmp_path / "bad2-1.0-py3-none-any.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr("mod.py", 'DEFAULT = "/var/nemeq/apiserver"\n')
        assert scan_wheels(tmp_path), "сканер колеса не увидел путь в тексте"


class TestLegacyTable:
    def test_all_legacy_paths_have_mapping(self):
        """M-9.1-i: каждый legacy-путь из §4.1 обязан иметь строку в таблице."""
        doc = REPO / "docs" / "architecture" / "NEMEQ_HOME_LAYOUT_v0.6.md"
        text = doc.read_text(encoding="utf-8")
        # Ключевые legacy-пути, упомянутые в спецификации
        required = ["/var/nemeq/apiserver/", "/var/nemeq/epishu/",
                    "/var/nemeq/state/", "/var/nemeq/data/", "/var/lib/nemeq/",
                    "/opt/nemeq/", "/run/nemeq/"]
        missing = [p for p in required if f"| `{p}`" not in text]
        assert not missing, f"нет строки в таблице §4.1: {missing}"
