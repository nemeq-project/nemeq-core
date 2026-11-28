"""Самопроверка инструментов критериев (v0.6 §9.10, мутанты M-9.10-*).

Смысл: инструменты, которые проверяют чужие критерии, сами должны быть
проверены — иначе они могут рапортовать успех, будучи сломанными. Именно так
в v0.4 `--coverage` пропустил документ с 45 мутантами при spec из 2 записей.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
CRIT = TOOLS / "crit_lint.py"
META = TOOLS / "metamutant.py"


def run(script: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(script), *args],
                       capture_output=True, text=True, timeout=180, cwd=str(REPO))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class TestCritLintSelftest:
    def test_selftest_passes(self):
        code, out = run(CRIT, "--selftest")
        assert code == 0, out[-800:]
        assert "PASS" in out

    def test_selftest_is_not_vacuous(self):
        """M-9.10-a: если линтер перестанет ловить отсутствующий мутант,
        его собственная фикстура обязана упасть.

        Проверяем прямым вызовом на документе без мутанта: линтер обязан найти
        проблему, а не промолчать.
        """
        bad = REPO / "docs" / "architecture" / "_tmp_no_mutant.md"
        try:
            bad.write_text(
                "### 8.1 Критерий без мутанта\n\nТекст без строки Мутант.\n",
                encoding="utf-8",
            )
            code, out = run(CRIT, str(bad))
            assert code == 1, f"линтер не нашёл критерий без мутанта: {out[-400:]}"
            assert "без мутанта" in out
        finally:
            bad.unlink(missing_ok=True)


class TestMetamutantSelftest:
    def test_selftest_passes(self):
        code, out = run(META, "--selftest")
        assert code == 0, out[-800:]
        assert "PASS" in out

    def test_skip_is_refusal_not_success(self, tmp_path):
        """M-9.10-c: SKIP (нет pytest) обязан быть кодом 3, а не 0.

        Запускаем selftest интерпретатором без pytest: подсовываем PYTHONPATH
        и -S, чтобы модуль не нашёлся.
        """
        r = subprocess.run(
            [sys.executable, "-S", str(META), "--selftest"],
            capture_output=True, text=True, timeout=180, cwd=str(REPO),
        )
        out = (r.stdout or "") + (r.stderr or "")
        if "SKIP" in out:
            assert r.returncode == 3, f"SKIP дал код {r.returncode}, ожидался 3: {out[-300:]}"
        else:
            # среда всё же нашла pytest — тогда SKIP не воспроизвести
            assert r.returncode == 0, out[-300:]


class TestCoverageDetectsProblems:
    """M-9.10-d/e/f/g: --coverage обязан падать в каждом из четырёх случаев."""

    DOC = REPO / "docs" / "architecture" / "NEMEQ_HOME_LAYOUT_v0.6.md"

    def _spec(self, tmp_path: Path, entries: list[dict]) -> Path:
        p = tmp_path / "spec.json"
        p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        return p

    def test_missing_entry_detected(self, tmp_path):
        """M-9.10-d: записи нет в spec → код 1 и имя ID."""
        code, out = run(META, "--coverage", str(self.DOC),
                        "--spec", str(self._spec(tmp_path, [])))
        assert code == 1
        assert "M-" in out

    def test_empty_test_field_detected(self, tmp_path):
        """M-9.10-e: пустое поле test → код 1."""
        text = self.DOC.read_text(encoding="utf-8")
        ids = re.findall(r"\*\*Мутант \[(M-[0-9A-Za-z.-]+)\]:\*\*", text)
        entries = [{"id": i, "test": ""} for i in ids]
        code, out = run(META, "--coverage", str(self.DOC),
                        "--spec", str(self._spec(tmp_path, entries)))
        assert code == 1
        assert "без исполняемого теста" in out

    def test_nonexistent_test_file_detected(self, tmp_path):
        """M-9.10-f: файл теста не существует → код 1."""
        text = self.DOC.read_text(encoding="utf-8")
        ids = re.findall(r"\*\*Мутант \[(M-[0-9A-Za-z.-]+)\]:\*\*", text)
        entries = [{"id": i, "test": "tests/p0/does_not_exist_xyz.py::test_x"}
                   for i in ids]
        code, out = run(META, "--coverage", str(self.DOC),
                        "--spec", str(self._spec(tmp_path, entries)))
        assert code == 1
        assert "не существует" in out or "does_not_exist" in out

    def test_empty_spec_is_error_not_success(self, tmp_path):
        """M-9.10-g: пустой список мутантов → код 2, а не 0.

        --root берём пустой каталог, а не REPO. С REPO мутация «пропустить
        проверку» уводит прогон на ВСЕ мутанты проекта: тест падает по
        таймауту, а не потому, что увидел код 0. Таймаут маскирует смысл —
        проверка должна быть быстрой и однозначной.
        """
        empty_root = tmp_path / "empty_root"
        empty_root.mkdir()
        code, out = run(META, "--spec", str(self._spec(tmp_path, [])),
                        "--root", str(empty_root))
        # Кода 2 недостаточно: его же отдаёт и «тесты не запускаются» при
        # мутации, то есть проверка не различает причины. Требуем именно
        # сообщение про пустой список.
        assert code == 2, f"пустой spec дал код {code}: {out[-300:]}"
        assert "список мутантов пуст" in out, (
            f"код 2 по другой причине — проверка не различает: {out[-300:]}"
        )


class TestToolsAreWired:
    def test_build_spec_exists(self):
        """Сводный spec собирается — иначе --coverage сверяет с заглушками."""
        assert (TOOLS / "build_spec.py").is_file()

    def test_real_specs_have_tests(self):
        """В блочных spec у каждой записи есть непустой test и существующий файл."""
        problems = []
        for spec_path in sorted((REPO / "docs" / "architecture").glob("metamutant_spec_*.json")):
            if spec_path.name in ("metamutant_spec_v05.json", "metamutant_spec_v06.json",
                                  "metamutant_spec_all.json"):
                continue
            for e in json.loads(spec_path.read_text(encoding="utf-8")):
                test = (e.get("test") or "").strip()
                if not test:
                    problems.append(f"{spec_path.name}: {e.get('id')} без test")
                    continue
                file_part = test.split("::")[0]
                if not (REPO / file_part).is_file():
                    problems.append(f"{spec_path.name}: {e.get('id')} -> {file_part} нет файла")
        assert not problems, "\n".join(problems[:10])
