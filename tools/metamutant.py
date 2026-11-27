#!/usr/bin/env python3
"""Метамутант: доказывает, что тест способен провалиться.

Причина появления: три итерации подряд сдавались критерии, которые не могли провалиться
(v0.1 §8.1 grep без -E; v0.2 §6.3 digest без хешей; v0.3 §7.3 digest с len() не в байтах).
Описанный мутант — это намерение. Проверка начинается с ПРОГОНА мутанта.

Идея: для каждой проверяемой функции хранится пара «мутант»: как сломать поведение.
Метамутант временно применяет мутант, запускает целевой тест и требует, чтобы тот
УПАЛ. Если тест остался зелёным — мутант вакуумный, критерий недействителен.

Использование:
    metamutant.py --selftest          # самопроверка движка на встроенном примере
    metamutant.py --spec <файл.json>  # прогон набора мутантов проекта

Формат spec (JSON):
    [{"id": "M1", "what": "почему этот мутант важен",
      "file": "path/to/module.py", "old": "...", "new": "...",
      "test": "tests/x.py::test_y"}]

Код возврата: 0 — все мутанты убиты (тесты упали), 1 — есть выжившие, 2 — ошибка.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PYTHON = sys.executable


def run_test(root: Path, target: str, timeout: int = 300) -> tuple[int, str]:
    """Прогон теста. Отличает «упал из-за мутанта» от «упал из-за инфраструктуры»."""
    import os

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run(
        [PYTHON, "-m", "pytest", target, "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(root), capture_output=True, text=True, timeout=timeout, env=env,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# Признаки того, что тест не запустился вовсе (инфраструктура, а не логика).
INFRA_MARKERS = (
    "No module named pytest",
    "No module named 'pytest'",
    "ModuleNotFoundError",
    "command not found",
    "ERROR: file or directory not found",
    "no tests ran",
    "errors during collection",
)


def is_infra_failure(out: str, test_target: str) -> bool:
    """Отличить «тест не запустился» от «тест упал по логике».

    Смотреть на весь stdout нельзя: pytest печатает локальные переменные и текст
    assert, поэтому подстрока из самого теста даёт ложное срабатывание.
    Признаём инфраструктурой только те случаи, когда НЕ выполнялся целевой тест:

      * `no tests ran` / `errors during collection` / `file or directory not found`
        — тест не дошёл до исполнения;
      * `ModuleNotFoundError: No module named pytest` — среда без pytest.

    Ключевое отличие: если в выводе есть `FAILED <target>`, значит тест
    ИСПОЛНЯЛСЯ и упал по логике — это убийство мутанта, а не инфраструктура.
    """
    text = out or ""
    lower = text.lower()

    # Тест реально исполнялся и упал по assert/исключению — это убийство.
    if f"failed {test_target}".lower() in lower:
        return False
    if test_target and test_target in text and "failed" in lower:
        return False

    # Не дошло до исполнения теста.
    for phrase in ("no tests ran", "errors during collection",
                   "file or directory not found", "command not found"):
        if phrase in lower:
            return True

    # Среда без pytest: проверяем по строке traceback, а не по вхождению
    # в произвольный текст.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("ModuleNotFoundError", "ImportError")):
            if "pytest" in stripped and "No module named" in stripped:
                return True

    return False


def baseline_ok(root: Path) -> tuple[bool, str]:
    """Базовый прогон ДО мутанта: если он красный, мутанты бессмысленны."""
    rc, out = run_test(root, ".")
    if rc == 0:
        return True, ""
    if is_infra_failure(out, "."):
        return False, "тесты не запускаются (инфраструктура)"
    return False, "базовый прогон уже красный (мутанты бессмысленны)"


def kill_mutant(root: Path, mutant: dict) -> tuple[bool, str]:
    """Применить мутант, прогнать тест, восстановить. True = тест упал (мутант убит)."""
    path = root / mutant["file"]
    if not path.is_file():
        return False, f"файл не найден: {mutant['file']}"
    original = path.read_text(encoding="utf-8")
    if mutant["old"] not in original:
        return False, f"якорь не найден в {mutant['file']}"
    mutated = original.replace(mutant["old"], mutant["new"], 1)
    path.write_text(mutated, encoding="utf-8")
    try:
        rc, out = run_test(root, mutant["test"])
        if rc == 0:
            return False, out  # тест зелёный → мутант выжил (вакуумный)
        if is_infra_failure(out, mutant["test"]):
            return False, "тест упал по инфраструктуре, не из-за мутанта"
        return True, out
    finally:
        path.write_text(original, encoding="utf-8")


def selftest() -> int:
    """Движок обязан: (1) убить настоящий мутант, (2) обнаружить вакуумный.

    Фикстура — маленький модуль с проверяемой функцией и тестом к ней.
    Мутант-1 меняет поведение (тест должен упасть) — настоящий.
    Мутант-2 меняет комментарий (тест остаётся зелёным) — вакуумный.
    """
    work = Path(tempfile.mkdtemp(prefix="metamutant-selftest-"))
    (work / "pkg").mkdir()
    (work / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (work / "pkg" / "calc.py").write_text(
        'def clamp(value: int, hi: int) -> int:\n'
        '    """Не даёт значению превысить hi."""\n'
        '    return value if value <= hi else hi\n',
        encoding="utf-8",
    )
    (work / "test_smoke.py").write_text(
        "from pkg.calc import clamp\n\n\n"
        "def test_clamp():\n"
        "    assert clamp(1, 10) == 1\n"
        "    assert clamp(99, 10) == 10\n",
        encoding="utf-8",
    )

    # Предусловие: без рабочего pytest движок не имеет права выдавать вердикт.
    ok, reason = baseline_ok(work)
    if not ok:
        shutil.rmtree(work, ignore_errors=True)
        print(f"SKIP selftest: {reason}")
        print("      Движок не проверен. В CI это ОТКАЗ, а не успех.")
        return 3
    real = {
        "id": "S1", "file": "pkg/calc.py",
        "old": "    return value if value <= hi else hi",
        "new": "    return value",
        "test": "test_smoke.py::test_clamp",
    }
    vacuous = {
        "id": "S2", "file": "pkg/calc.py",
        "old": '"""Не даёт значению превысить hi."""',
        "new": '"""Не даёт значению превысить лимит."""',
        "test": "test_smoke.py::test_clamp",
    }
    print("--- движок: настоящий мутант (тест обязан упасть)")
    killed, out = kill_mutant(work, real)
    print(f"{'OK  ' if killed else 'FAIL'} мутант S1 убит={killed}")
    print("--- движок: вакуумный мутант (тест обязан остаться зелёным)")
    killed2, _ = kill_mutant(work, vacuous)
    print(f"{'OK  ' if not killed2 else 'FAIL'} мутант S2 убит={killed2} (ожидалось False — значит вакуумный)")
    shutil.rmtree(work, ignore_errors=True)
    ok = killed and not killed2
    print(f"\nselftest: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def check_spec_coverage(doc: Path, spec_path: Path) -> list[str]:
    """Каждый мутант документа должен иметь ID и запись в spec.

    Иначе возможен ложнозелёный прогон: в документе 45 мутантов, в spec описано 2,
    оба убиты — и оба инструмента рапортуют успех, хотя 43 критерия не проверены.
    """
    problems: list[str] = []
    text = doc.read_text(encoding="utf-8")
    ids = set(re.findall(r"\[(M-[0-9A-Za-z.-]+)\]", text))
    if not ids:
        return [f"{doc.name}: ни у одного мутанта нет ID вида [M-...] — сверка с spec невозможна"]
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec_ids = {m.get("id") for m in spec}
    missing = sorted(ids - spec_ids)
    if missing:
        problems.append(f"{doc.name}: мутанты без записи в spec: {', '.join(missing)}")
    extra = sorted(spec_ids - ids)
    if extra:
        problems.append(f"{spec_path.name}: записи без мутанта в документе: {', '.join(extra)}")
    # Наличие ID не доказывает исполнение: у записи обязан быть непустой test,
    # и файл теста должен существовать. Иначе 105 мутантов описаны, а исполняются два.
    no_test = [m.get("id") for m in spec if not (m.get("test") or "").strip()]
    if no_test:
        problems.append(
            f"{spec_path.name}: записи без исполняемого теста: {len(no_test)} "
            f"({', '.join(no_test[:5])}{'...' if len(no_test) > 5 else ''})"
        )

    # Файл теста обязан существовать: иначе «исполняемый» тест — фикция.
    root = spec_path.parent.parent.parent
    missing_files = []
    for m in spec:
        test = (m.get("test") or "").strip()
        if not test:
            continue
        file_part = test.split("::")[0]
        if not (root / file_part).is_file():
            missing_files.append(f"{m.get('id')} -> {file_part}")
    if missing_files:
        problems.append(
            f"{spec_path.name}: файл теста не существует: {len(missing_files)} "
            f"({'; '.join(missing_files[:3])}{'...' if len(missing_files) > 3 else ''})"
        )

    if not spec:
        problems.append(f"{spec_path.name}: spec пуст — вердикт невозможен")
    return problems


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if "--selftest" in sys.argv:
        return selftest()
    if "--coverage" in sys.argv:
        doc = Path(sys.argv[sys.argv.index("--coverage") + 1])
        spec = Path(sys.argv[sys.argv.index("--spec") + 1])
        problems = check_spec_coverage(doc, spec)
        if problems:
            for p in problems:
                print(p)
            print("metamutant --coverage: FAIL")
            return 1
        print("metamutant --coverage: OK")
        return 0
    if "--spec" not in sys.argv:
        print(__doc__)
        return 2
    spec_path = Path(sys.argv[sys.argv.index("--spec") + 1])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not spec:
        print(f"{spec_path.name}: список мутантов пуст — прогон невозможен (код 2)")
        return 2
    root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve() if "--root" in sys.argv else Path.cwd()

    # Нельзя выдавать вердикт, если тесты вообще не запускаются: тогда «мутанты
    # убиты» означало бы только то, что среда сломана, а не что критерии сильны.
    ok, reason = baseline_ok(root)
    if not ok:
        print(f"ОТКАЗ: {reason}")
        print("metamutant: без работающего pytest вердикт невозможен (код 2)")
        return 2

    survivors = []
    for m in spec:
        killed, out = kill_mutant(root, m)
        label = f"{m['id']} {m.get('what', '')[:60]}"
        if killed:
            print(f"[KILLED]  {label}")
        else:
            print(f"[SURVIVED] {label}")
            survivors.append(m)
            tail = out.strip().splitlines()[-3:] if out else []
            for t in tail:
                print("           ", t[:120])
    print(f"\nmetamutant: killed {len(spec) - len(survivors)}/{len(spec)}")
    if survivors:
        print("ВАКУУМНЫЕ (критерий недействителен):", ", ".join(m["id"] for m in survivors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
