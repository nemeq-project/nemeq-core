#!/usr/bin/env python3
"""Dependency-free lint for nemeq-core.

Checks:
- every tracked Python file compiles;
- imports are stdlib or local packages (pytest is allowed in tests);
- no ``shell=True`` subprocess call.

The goal is a CI gate without adding ruff/black/flake8 to the runtime.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "run"}
LOCAL_PACKAGES = {"kernel", "agents", "massaru", "nqctl", "tests", "tools"}
DEV_ALLOWED = {"pytest"}


def iter_python_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def check_file(path: Path, allowed: set[str]) -> list[str]:
    errors: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        compile(source, str(path), "exec")
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [f"{path}: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in allowed:
                    errors.append(f"{path}: non-stdlib import {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                top = node.module.split(".")[0]
                if top not in allowed:
                    errors.append(
                        f"{path}: non-stdlib import from {node.module!r}"
                    )
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "shell" and isinstance(
                    keyword.value, ast.Constant
                ) and keyword.value.value is True:
                    errors.append(f"{path}: shell=True is forbidden")
    return errors


def main() -> int:
    allowed = (
        set(sys.stdlib_module_names)
        | LOCAL_PACKAGES
        | DEV_ALLOWED
        | {"__future__"}
    )
    files = iter_python_files()
    errors: list[str] = []
    for path in files:
        errors.extend(check_file(path, allowed))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"lint: {len(errors)} error(s) in {len(files)} files")
        return 1
    print(f"lint: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
