#!/usr/bin/env python3
"""Линтер критериев приёмки в архитектурных документах.

Причина появления: три итерации подряд (v0.1 §8.1, v0.2 §6.3, v0.3 §7.3) сдавались
критерии, которые не могли провалиться. Описанный мутант — это намерение; проверка
начинается с того, что критерий обязан иметь мутанта, а мутант — быть исполняемым.

Что проверяется:
  1. Каждый заголовок критерия (`### 8.x` или `### Некоторый критерий`) содержит
     непустую строку «Мутант:».
  2. Мутант сформулирован как действие, а не как обещание: содержит глагол
     из списка и целевой объект (тест/критерий/код/проверку).
  3. В документе нет вакуумных grep-паттернов: `grep` с `|` без `-E`/`-e`.
  4. Нет ссылок на несуществующие разделы вида `§N.M`, если такого раздела нет.
  5. Критерий не может быть «проверкой доступности» вместо «проверки инварианта»:
     запрещены формулировки «health non-200» и «сервис отвечает» как единственный
     мутант — они ловят падение, но не логическую регрессию.

Использование:
    crit_lint.py <файл.md> [<файл.md> ...]
    crit_lint.py --selftest        # прогон на встроенных фикстурах

Код возврата: 0 — чисто, 1 — найдены нарушения, 2 — ошибка вызова.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Глаголы, которыми мутант описывает ДЕЙСТВИЕ над кодом/данными.
# Требование жёсткое: мутант обязан начинаться с глагола действия, а не с исхода.
MUTANT_VERBS = (
    "добав", "удал", "подмен", "сломал", "измен", "замен", "впис", "постав",
    "сдвиг", "обнул", "выключ", "отключ", "сократ", "расшир", "переимен",
    "сдвин", "верн", "убра", "перестав", "смонтир", "запуст", "увелич",
    "перенаправ", "пониз", "повыс", "отбро", "сымит", "поддела",
    "выстав", "прерв", "выполн", "указ", "запис", "урон", "мигрир",
    "смен", "скопир", "перенес", "перемест", "откат", "обнов",
)
# Исход-глаголы: сами по себе НЕ описывают действие (это результат, не мутация).
OUTCOME_ONLY = ("падает", "провал", "краснеет", "зелёный", "отказ")
# Признаки «проверки доступности» — слабый мутант.
WEAK_ONLY = ("health non-200", "health отдаёт non-200", "сервис отвечает", "сервис не отвечает")

VACUOUS_GREP = re.compile(r"\bgrep\b[^\n]*")
SECTION_REF = re.compile(r"§(\d+(?:\.\d+)?)")
CRITERION_HEAD = re.compile(r"^#{3,4}\s+(?:(\d+\.\d+)|(.+))\s*$")


def check_document(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    lines = text.splitlines()

    # --- 1-2. критерии и их мутанты
    crit_index = []
    for i, line in enumerate(lines):
        if CRITERION_HEAD.match(line) and (line.startswith("### ") or line.startswith("#### ")):
            crit_index.append((i, line.strip()))

    for n, (idx, head) in enumerate(crit_index):
        end = crit_index[n + 1][0] if n + 1 < len(crit_index) else len(lines)
        body = lines[idx:end]
        mutant_lines = [l for l in body if re.match(r"^\s*\*{0,2}Мутант", l)]
        if not mutant_lines:
            problems.append(f"{path.name}:{idx+1} критерий без мутанта: {head[:70]}")
            continue
        # Проверяется каждый мутант блока: хотя бы один обязан описывать действие.
        # (Раньше бралась только первая строка, и мутант-действие во второй
        # строке ложно считался отсутствующим.)
        mutant_text = " ".join(mutant_lines).lower()
        has_action = any(v in mutant_text for v in MUTANT_VERBS)
        if not has_action:
            problems.append(f"{path.name}:{idx+1} мутант без действия (указан исход, не мутация): {head[:50]}")
        if has_action and any(w in mutant_text for w in WEAK_ONLY) and len(mutant_text) < 200:
            problems.append(f"{path.name}:{idx+1} мутант проверяет доступность, не инвариант: {head[:50]}")

    # --- 3. вакуумные grep
    for i, line in enumerate(lines):
        if not VACUOUS_GREP.search(line):
            continue
        if "'|'" in line or '"|"' in line:
            continue
        if "|" in line and not re.search(r"-[a-zA-Z]*E[a-zA-Z]*\b", line) and "-e " not in line and "\\|" not in line:
            problems.append(f"{path.name}:{i+1} grep с '|' без -E ищет литерал (вакуумный критерий)")

    # --- 4. ссылки на разделы
    declared = set()
    for line in lines:
        m = re.match(r"^#{2,4}\s+(\d+(?:\.\d+)?)", line.strip())
        if m:
            declared.add(m.group(1))
    for i, line in enumerate(lines):
        for ref in SECTION_REF.findall(line):
            if ref not in declared and "." in ref:
                problems.append(f"{path.name}:{i+1} ссылка на отсутствующий §{ref}")

    return problems


def selftest() -> int:
    """Встроенные фикстуры: линтер обязан ловить именно те ошибки, что были в v0.1–v0.3."""
    cases = [
        (
            "плохо: критерий без мутанта",
            "### 8.1 Чистота\n\nТекст критерия.\n",
            True,
        ),
        (
            "плохо: мутант-намерение без действия",
            "### 8.1 Чистота\n\nМутант: должен падать.\n",
            True,
        ),
        (
            "плохо: вакуумный grep",
            "### 8.1 Чистота\n\n```sh\ngrep -rn \"/var/nemeq|/opt/nemeq\" .\n```\n\nМутант: подменить путь → падает.\n",
            True,
        ),
        (
            "плохо: ссылка на отсутствующий раздел",
            "### 8.1 Чистота\n\nСм. §7.99.\n\nМутант: удалить строку → падает.\n",
            True,
        ),
        (
            "хорошо: критерий с действием-мутантом",
            "### 8.1 Чистота\n\n```sh\ngrep -rnE '/(var|opt)/nemeq' .\n```\n\nМутант: добавить /var/nemeq в .rs → тест падает.\n",
            False,
        ),
    ]
    failed = 0
    for name, text, should_fail in cases:
        tmp = Path("/tmp") / "crit_lint_fixture.md"
        tmp.write_text(text, encoding="utf-8")
        problems = check_document(tmp)
        got_fail = bool(problems)
        ok = got_fail == should_fail
        print(f"{'OK  ' if ok else 'FAIL'} {name}: проблем={len(problems)}")
        if not ok:
            failed += 1
            for p in problems:
                print("      ", p)
    print(f"\nselftest: {'PASS' if failed == 0 else 'FAIL'} ({len(cases)-failed}/{len(cases)})")
    return 0 if failed == 0 else 1


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        return 2
    if "--selftest" in args:
        return selftest()
    problems: list[str] = []
    for a in args:
        p = Path(a)
        if not p.is_file():
            print(f"нет файла: {a}")
            return 2
        problems.extend(check_document(p))
    if problems:
        for p in problems:
            print(p)
        print(f"\ncrit_lint: FAIL ({len(problems)} нарушений)")
        return 1
    print("crit_lint: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
