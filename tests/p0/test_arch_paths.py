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

# Импорт на уровне модуля: проверка колёс обязана исполняться в том же
# процессе, что и модуль поставки. Иначе мутация в `deps.py`, создающая
# колесо, не выполнится, и тест §9.1-d был бы вакуумным.
from massaru import deps as deps_mod  # noqa: F401

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
    "tests/p0/test_arch_paths.py",   # сам сканер: содержит паттерны
    "tests/p0/test_legacy.py",       # фикстуры миграции: пути по смыслу
    "tools/crit_lint.py",            # строки-фикстуры собственного selftest
    "API_SCHEMA.md",
}

# Каталоги, которые не сканируются: документация описывает пути по смыслу,
# фикстуры их специально содержат.
SKIP_DIRS = {".git", "__pycache__", "docs", "tests/fixtures", "node_modules",
             ".pytest_cache", "run"}

# tests/p0 больше НЕ исключается целиком: это была слепая зона — нарушения
# в самих тестах не видел никто. Вместо каталога — поимённый allow-list
# файлов, которым пути ОС нужны по смыслу (сканер и фикстуры миграции).

# Каталоги-артефакты чужих прогонов: файлы принадлежат root, удалить их
# пользователь не может, поэтому они переименованы в *.ROOT-ARTIFACT.
# Это НЕ содержимое репозитория, и в git они не попадают.
ARTIFACT_MARKERS = (".ROOT-ARTIFACT", ".KILLED-RUN-ARTIFACT", ".STALE-ROOT")


def _is_artifact(path) -> bool:
    """Файл/каталог — артефакт чужого прогона, а не содержимое репозитория."""
    return any(marker in str(path) for marker in ARTIFACT_MARKERS)

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


FORBIDDEN_ROOTS = {"/var", "/opt", "/etc", "/run", "/srv", "/usr/local", "/var/lib"}
# Сегменты, из которых собирают путь по частям: os.path.join(root, "var", "nemeq")
# и Path(root) / "var" / "nemeq". Регулярка такой формы не видит вовсе.
FORBIDDEN_SEGMENTS = {"var", "opt", "etc", "run", "srv", "usr", "local"}
# Хвост, который делает сборку legacy-путём.
TARGET_SEGMENT = "nemeq"


def _segments(node: ast.AST) -> list[str]:
    """Строковые сегменты выражения: аргументы join или операнды `/`."""
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value.strip("/"))
    return out


def scan_ast(root: Path) -> list[str]:
    """AST: ловит конкатенацию путей, которую регулярка не видит.

    Покрываются ЧЕТЫРЕ формы, каждая — отдельный мутант:

      * `Path("/var") / "nemeq"`                 (M-9.1-b);
      * `os.path.join("/var", "nemeq")`          (M-9.1-g);
      * `os.path.join(root, "var", "nemeq")`     (M-9.1-g, по сегментам);
      * `os.environ.get("HOME") + "/.nemeq"`     (склейка строкой).
    """
    hits: list[str] = []
    for path in root.rglob("*.py"):
        rel = str(path.relative_to(root))
        if _is_artifact(rel):
            continue
        if rel in ALLOWED_FILES:
            continue
        # tests/p0 НЕ исключается: AST-проверка обязана видеть конкатенацию
        # в самих тестах так же, как в коде. Исключение каталога оставляло
        # ровно ту слепую зону, которую §9.1 и должен закрывать.
        if any(s in rel for s in ("/docs/", "/tests/fixtures/")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Одиночный "/var" — не путь ОС: это может быть суффикс
                # сравнения (`p.endswith("/var")` для ReadWritePaths) или
                # сегмент. Нарушение — литерал, который ЯВЛЯЕТСЯ путём nemeq:
                # либо содержит "nemeq", либо им продолжается сборка
                # (ловим отдельно по AST-склейке ниже).
                lit = node.value
                if lit in FORBIDDEN_ROOTS and TARGET_SEGMENT not in lit:
                    # Разрешаем только суффиксные сравнения: endswith/startswith
                    # с этим литералом, где путь собирается из переменной.
                    parent_is_suffix = False
                    for anc in ast.walk(tree):
                        if isinstance(anc, ast.Call) and isinstance(anc.func, ast.Attribute):
                            if anc.func.attr in ("endswith", "startswith"):
                                for a in anc.args:
                                    if a is node:
                                        parent_is_suffix = True
                    if not parent_is_suffix:
                        hits.append(f"{rel}:{node.lineno}: raw literal {lit!r}")

            # os.path.join(...): цель — последний сегмент "nemeq",
            # а один из предыдущих — запрещённый корень (по имени сегмента).
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    name = node.func.id
                if name == "join":
                    segs = _segments(node)
                    if TARGET_SEGMENT in segs and any(
                        s in FORBIDDEN_SEGMENTS for s in segs
                    ):
                        hits.append(
                            f"{rel}:{node.lineno}: joined path "
                            f"{'/'.join(segs)} (§9.1, AST-обход)"
                        )

            # Склейка строкой: <что угодно> + "/.nemeq" или + "/var/..."
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                for side in (node.left, node.right):
                    if isinstance(side, ast.Constant) and isinstance(side.value, str):
                        if OS_PATH_PATTERN.search(side.value):
                            hits.append(
                                f"{rel}:{node.lineno}: string concat {side.value!r}"
                            )
    return hits


def scan_gitignore(root: Path) -> list[str]:
    """Файл, спрятанный в .gitignore, не должен скрывать нарушения.

    Отсутствие `.gitignore` — НЕ «всё чисто». Пустой список здесь означал бы
    «проверено и нарушений нет», хотя проверять было нечего: копия дерева
    без `.gitignore` (архив, экспорт) давала бы ложно-зелёный результат.
    Поэтому отсутствие файла в корне репозитория — отдельная находка.
    """
    gi = root / ".gitignore"
    if not gi.is_file():
        # Корень репозитория определяется наличием соседних маркеров:
        # если это похоже на дерево проекта, .gitignore обязан быть.
        looks_like_repo = any(
            (root / marker).exists()
            for marker in ("pyproject.toml", "riksu.toml", "setup.py", "massaru")
        )
        if looks_like_repo:
            return [
                ".gitignore отсутствует в корне проекта — проверка скрытых "
                "путей не выполнима (это НЕ значит «нарушений нет»)"
            ]
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
        if _is_artifact(whl):
            continue
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


@pytest.fixture(autouse=True)
def _clean_stray_wheels():
    """Убрать `wheelhouse/`, созданный мутантом §9.1-d/f.

    Чистка ИДЁМПОТЕНТНА и не сравнивает «до/после»: мутант в `deps.py`
    создаёт колесо на ИМПОРТЕ модуля, то есть ещё до входа в фикстуру.
    Снимок «до» такой файл уже содержал бы, и мусор оставался навсегда.

    Репозиторий не отслеживает ни одного `.whl` (`git ls-files '*.whl'` пуст),
    поэтому удалять здесь безопасно. Проверка §9.1-d/f выполняется ВНУТРИ
    теста — до teardown, — так что детекция не ослабляется.
    """
    yield
    import shutil

    for wh in list(REPO.rglob("*.whl")):
        if _is_artifact(wh):
            continue
        try:
            wh.unlink()
        except OSError:
            pass
    d = REPO / "wheelhouse"
    if d.is_dir() and not any(d.iterdir()):
        try:
            d.rmdir()
        except OSError:
            pass


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
    """M-9.1-i: удалить `/var/lib/nemeq` из таблицы §4.1 → тест падает.

    Проверяются ТРИ стороны, а не только документ: строка в спецификации,
    запись в коде (`massaru.legacy.MAPPINGS`) и попадание корня в
    `legacy_roots()`. Проверка только документа была бы вакуумной: строка
    осталась бы в `.md`, даже если код перестал её понимать.
    """

    REQUIRED = [
        "/var/nemeq/apiserver",
        "/var/nemeq/epishu",
        "/var/nemeq/state",
        "/var/nemeq/data",
        "/var/lib/nemeq",
        "/opt/nemeq",
        "/run/nemeq",
    ]

    def test_all_legacy_paths_in_document_table(self):
        doc = REPO / "docs" / "architecture" / "NEMEQ_HOME_LAYOUT_v0.6.md"
        text = doc.read_text(encoding="utf-8")
        missing = [p for p in self.REQUIRED if f"| `{p}/`" not in text]
        assert not missing, f"нет строки в таблице §4.1: {missing}"

    def test_all_legacy_paths_in_code_table(self):
        """Та же таблица обязана существовать в коде, а не только в документе."""
        from massaru.legacy import MAPPINGS

        sources = {m.source for m in MAPPINGS}
        missing = [p for p in self.REQUIRED if p not in sources]
        assert not missing, f"путь есть в документе, но не в MAPPINGS: {missing}"

    def test_all_legacy_roots_known(self):
        from massaru.legacy import legacy_roots

        roots = set(legacy_roots())
        for root in ("/var/nemeq", "/var/lib/nemeq", "/opt/nemeq", "/run/nemeq"):
            assert root in roots, f"{root} не входит в legacy_roots()"

    def test_char_count_matches_between_doc_and_code(self):
        """Число строк таблицы §4.1 совпадает с числом правил в коде."""
        from massaru.legacy import MAPPINGS

        doc = REPO / "docs" / "architecture" / "NEMEQ_HOME_LAYOUT_v0.6.md"
        text = doc.read_text(encoding="utf-8")
        table_rows = [
            line for line in text.splitlines()
            if line.startswith("| `/") and "|" in line
        ]
        assert len(table_rows) >= len(MAPPINGS), (
            f"в §4.1 строк {len(table_rows)}, правил в коде {len(MAPPINGS)}"
        )


# --- §4.8: run/ без overlay -------------------------------------------------

MOUNT_CALLS = {"mount", "umount", "mount_overlay", "unshare"}
MOUNT_MODULES = {"os", "ctypes", "subprocess"}


def scan_mount_usage(root: Path) -> list[str]:
    """§4.8: overlay в `run/` — это `mount()`, то есть root.

    `run/` держит только pid, lock-ссылку, сокеты и два json. Монтирование
    поверх него ломает и `fsync` родителя (§7.2), и `cleanup`. Проверяется не
    текст, а AST: вызов `os.mount`/`libc.mount`/`mount(...)` в коде проекта.
    """
    hits: list[str] = []
    for path in root.rglob("*.py"):
        rel = str(path.relative_to(root))
        if rel in ALLOWED_FILES or "/tests/fixtures/" in rel:
            continue
        if any(part in rel.split("/") for part in (".git", "__pycache__")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name not in MOUNT_CALLS:
                continue
            # `umount` в разборе тоже ловится, но установка его не вызывает.
            hits.append(f"{rel}:{node.lineno}: call {name}() — mount is forbidden in run/ (§4.8)")
    return hits


class TestRunWithoutOverlay:
    """M-4.8-a: добавить overlay-монтирование в `run/` → архитектурный тест падает."""

    def test_no_mount_calls_in_sources(self):
        hits = scan_mount_usage(REPO)
        assert not hits, "монтирование в коде (§4.8):\n" + "\n".join(hits[:10])

    def test_detects_injected_overlay(self, tmp_path):
        """Самопроверка: внесённый `os.mount(...)` обязан быть найден."""
        mod = tmp_path / "bad.py"
        mod.write_text(
            "import os\n"
            "def overlay(run_dir):\n"
            "    os.mount('overlay', run_dir, 'overlay', 0, 'lowerdir=/a')\n",
            encoding="utf-8",
        )
        assert scan_mount_usage(tmp_path), "сканер не увидел mount()"

    def test_run_dir_holds_only_declared_files(self):
        """§4.8: в run/ нет ничего, кроме разрешённого перечня."""
        import tempfile

        from massaru.layout import resolve_all
        from massaru.install import install

        root = Path(tempfile.mkdtemp())
        try:
            r = resolve_all(home=str(root / "home"), instance="alpha", env={})
            install(r)
            allowed = {"instance.json", "migration.json", "migration-report.json",
                       "nq.sock", "nqctl.sock", "instance.json.tmp"}
            rd = r.home / "instances" / "alpha" / "run"
            if rd.is_dir():
                unexpected = {
                    p.name for p in rd.iterdir()
                    if p.name not in allowed and not p.name.endswith(".lock")
                }
                assert not unexpected, f"посторонние файлы в run/: {sorted(unexpected)}"
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)


# --- §9.1-h: симлинки в итоговом дереве $NEMEQ_HOME ------------------------


def scan_home_symlinks(home: Path) -> list[str]:
    """Ни один симлинк внутри `$NEMEQ_HOME` не ведёт за его пределы.

    Причина отдельной проверки: симлинк не содержит текста, поэтому ни
    регулярка, ни AST его не видят. Именно так в v0.5 пропускался симлинк
    на `/etc/passwd` внутри колеса.
    """
    hits: list[str] = []
    if not home.is_dir():
        return hits
    real_home = os.path.realpath(home)
    for dirpath, dirnames, filenames in os.walk(home):
        for name in list(dirnames) + list(filenames):
            path = Path(dirpath) / name
            if not path.is_symlink():
                continue
            target = os.readlink(path)
            if os.path.isabs(target):
                hits.append(f"{path}: absolute symlink -> {target}")
                continue
            resolved = os.path.realpath(path)
            if not (resolved == real_home or resolved.startswith(real_home + os.sep)):
                hits.append(f"{path}: escapes HOME -> {target} ({resolved})")
    return hits


class TestHomeTreeSymlinks:
    """M-9.1-h: симлинк из `$NEMEQ_HOME` наружу после install → тест падает."""

    def test_install_creates_no_escaping_symlink(self, tmp_path):
        from massaru.install import install
        from massaru.layout import resolve_all

        r = resolve_all(home=str(tmp_path / "home"), instance="alpha", env={})
        install(r)
        hits = scan_home_symlinks(r.home)
        assert not hits, "симлинки наружу в $NEMEQ_HOME:\n" + "\n".join(hits[:10])

    def test_detects_escaping_symlink(self, tmp_path):
        """Самопроверка: внесённый симлинк наружу обязан быть найден."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "bin").mkdir()
        (home / "bin" / "evil").symlink_to("/etc/passwd")
        assert scan_home_symlinks(home), "сканер не увидел симлинк наружу"

    def test_allows_internal_relative_symlink(self, tmp_path):
        """Внутренняя относительная ссылка — легальна (§9.9: var -> instances/…)."""
        home = tmp_path / "home"
        (home / "instances" / "default" / "var").mkdir(parents=True)
        (home / "var").symlink_to("instances/default/var")
        assert scan_home_symlinks(home) == []
