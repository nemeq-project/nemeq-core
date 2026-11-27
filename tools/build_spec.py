"""Собрать сводный spec из блочных spec-файлов и сверить с документом.

Проблема, которую решает: `metamutant_spec_v06.json` содержит 105 заглушек
(«not-implemented»), а реальные мутанты живут в блочных файлах
(spec_layout, spec_digest, ...). `--coverage` сверяет ID документа с ОДНИМ
spec-файлом, поэтому видел заглушки и падал.

Собираем реальные записи в один файл и честно показываем, какие ID документа
ещё не покрыты исполняемыми тестами.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SPECS_DIR = Path("docs/architecture")
DOC = SPECS_DIR / "NEMEQ_HOME_LAYOUT_v0.6.md"
OUT = SPECS_DIR / "metamutant_spec_all.json"


def main() -> int:
    blocks = sorted(SPECS_DIR.glob("metamutant_spec_*.json"))
    real: dict[str, dict] = {}
    for path in blocks:
        if path.name in ("metamutant_spec_v05.json", "metamutant_spec_v06.json",
                         "metamutant_spec_all.json"):
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for e in entries:
            mid = e.get("id")
            test = (e.get("test") or "").strip()
            if mid and test:
                real[mid] = e

    text = DOC.read_text(encoding="utf-8")
    doc_ids = re.findall(r"\*\*Мутант \[(M-[0-9A-Za-z.-]+)\]:\*\*", text)

    covered = sorted(set(doc_ids) & set(real))
    missing = sorted(set(doc_ids) - set(real))

    OUT.write_text(json.dumps([real[i] for i in covered], ensure_ascii=False, indent=1),
                   encoding="utf-8")

    print(f"мутантов в документе : {len(doc_ids)} (уникальных {len(set(doc_ids))})")
    print(f"покрыто тестами      : {len(covered)}")
    print(f"не покрыто           : {len(missing)}")
    print(f"сводный spec         : {OUT}")
    if missing:
        print("\nне покрыты:")
        for mid in missing:
            print(" ", mid)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
