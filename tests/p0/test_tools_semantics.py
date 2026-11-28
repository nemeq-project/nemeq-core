"""Дополнение к test_tools_selftest.py: точные проверки SKIP и инфраструктуры.

Прежние тесты были вакуумными:
  * M-9.10-c выживал, потому что запуск через `-S` не гарантировал SKIP-ветку,
    и тест шёл по ветке «pytest нашёлся» (там ждётся rc=0 — что мутант и даёт);
  * M-9.10-b выживал, потому что `test_selftest_passes` проверял только успех,
    а не способность отличить инфраструктурное падение от логического.

Здесь оба проверяются прямой подменой функций модуля — точно и без догадок.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
META = REPO / "tools" / "metamutant.py"


def _load_meta():
    spec = importlib.util.spec_from_file_location("metamutant_under_test", META)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSkipIsRefusal:
    def test_skip_returns_code_3(self, tmp_path, monkeypatch):
        """M-9.10-c: SKIP обязан быть кодом 3, а не 0.

        Подменяем baseline_ok — это ровно та точка, где движок узнаёт, что
        pytest недоступен. Мутант `return 0` здесь падает.
        """
        mm = _load_meta()
        monkeypatch.setattr(mm, "baseline_ok", lambda root: (False, "нет pytest"))
        code = mm.selftest()
        assert code == 3, f"SKIP дал код {code}, ожидался 3 (отказ, не успех)"


class TestInfraFailureNotAKill:
    def test_infra_error_is_not_counted_as_killed(self, tmp_path, monkeypatch):
        """M-9.10-b: падение теста по инфраструктуре не должно считаться убийством.

        Если убрать различение, `kill_mutant` объявит мутант убитым — и любой
        сломанный CI будет выглядеть как «все мутанты пойманы».
        """
        mm = _load_meta()

        target = tmp_path / "mod.py"
        target.write_text("x = 1\n", encoding="utf-8")

        # Тест «падает», но причина — отсутствие pytest, а не мутация.
        # Строка собирается из частей: иначе сам литерал попадёт в traceback,
        # и детектор инфраструктуры сработает на собственном исходнике теста
        # (именно из-за этого мутант выживал).
        infra_msg = "ModuleNotFoundError: No module named " + "py" + "test"
        monkeypatch.setattr(
            mm, "run_test",
            lambda root, t, timeout=300: (1, infra_msg),
        )

        mutant = {
            "id": "X1", "file": "mod.py",
            "old": "x = 1", "new": "x = 2",
            "test": "tests/x.py::test_x",
        }
        killed, out = mm.kill_mutant(tmp_path, mutant)
        # ВАЖНО: сообщение assert не должно содержать текст `out`.
        # Иначе при мутанте в выводе теста появится «No module named pytest»,
        # метамутант увидит инфра-маркер в stdout и засчитает тест как
        # инфраструктурное падение — мутант «выживет» ложно.
        assert killed is False
        assert "инфраструктур" in str(out).lower()

    def test_real_failure_is_counted_as_killed(self, tmp_path, monkeypatch):
        """Обратная сторона: настоящее падение теста обязано считаться убийством."""
        mm = _load_meta()

        target = tmp_path / "mod.py"
        target.write_text("x = 1\n", encoding="utf-8")

        monkeypatch.setattr(
            mm, "run_test",
            lambda root, t, timeout=300: (1, "FAILED tests/x.py::test_x - assert 1 == 2"),
        )

        mutant = {
            "id": "X2", "file": "mod.py",
            "old": "x = 1", "new": "x = 2",
            "test": "tests/x.py::test_x",
        }
        killed, _ = mm.kill_mutant(tmp_path, mutant)
        assert killed is True, "настоящее падение не засчитано"
