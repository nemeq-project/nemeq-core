#!/usr/bin/env python3
"""Mutation run for the v1 massaru skeleton.

The v1 slice is read-only; these mutants prove that the skeleton tests can
fail on the promises that matter now: path safety, no-write list, no-write
stubs, and the 02b layout key set.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PYTHON = sys.executable

MUTANTS: list[dict[str, str]] = [
    {
        "id": "M1",
        "name": "safe_epishu_id_accepts_traversal",
        "file": "massaru/layout.py",
        "test": "TestSafeEpishuId",
        "old": """    if not isinstance(epishu_id, str) or not _SAFE_ID.fullmatch(epishu_id):
        raise ValueError("invalid epishu_id")
    if epishu_id in {".", ".."}:
        raise ValueError("invalid epishu_id")
    return epishu_id""",
        "new": """    return epishu_id""",
    },
    {
        "id": "M2",
        "name": "list_creates_state_root",
        "file": "massaru/store.py",
        "test": "test_list_missing_root_does_not_create_state",
        "old": """    root = epishu_root(state_root)
    if not root.is_dir():
        return []""",
        "new": """    root = epishu_root(state_root)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        return []""",
    },
    {
        "id": "M3",
        "name": "lifecycle_stub_writes_state",
        "file": "massaru/cli.py",
        "test": "test_lifecycle_stub_no_side_effects",
        "old": """    if args.command in LIFECYCLE_COMMANDS:
        emit(""",
        "new": """    if args.command in LIFECYCLE_COMMANDS:
        state_root.mkdir(parents=True, exist_ok=True)
        emit(""",
    },
    {
        "id": "M4",
        "name": "layout_omits_wal",
        "file": "massaru/layout.py",
        "test": "test_layout_paths_keys",
        "old": """    "wal",
    "atoms",""",
        "new": """    "atoms",""",
    },
    {
        "id": "M5",
        "name": "reconcile_ignores_digest_mismatch",
        "file": "massaru/reconcile.py",
        "test": "test_digest_mismatch_rejects_without_layout",
        "old": """            observation.actual_digest is None
            or observation.actual_digest != observation.expected_digest""",
        "new": """            observation.actual_digest is None
            or False""",
    },
    {
        "id": "M6",
        "name": "bind_model_mutates_input_spec",
        "file": "massaru/reconcile.py",
        "test": "test_bind_model_is_pure_spec_update",
        "old": """    return replace(spec, model=model)""",
        "new": """    spec.model = model
    return spec""",
    },
    {
        "id": "M7",
        "name": "reconcile_allows_writable_epishu",
        "file": "massaru/reconcile.py",
        "test": "test_read_only_and_kind_guards",
        "old": """    if spec.read_only is not True:""",
        "new": """    if False:""",
    },
    {
        "id": "M8",
        "name": "store_skips_cas",
        "file": "massaru/store.py",
        "test": "test_cas_conflict",
        "old": """            if (
                request.expected_rev is not None
                and request.expected_rev != current_rev
            ):""",
        "new": """            if (
                False and request.expected_rev is not None
                and request.expected_rev != current_rev
            ):""",
    },
    {
        "id": "M9",
        "name": "store_skips_prev_check",
        "file": "massaru/store.py",
        "test": "test_corrupt_prev_chain_fails",
        "old": """            if prev != expected_hash:
                report.errors.append(f"prev mismatch at rev {rev}")
                break""",
        "new": """            if False:
                report.errors.append(f"prev mismatch at rev {rev}")
                break""",
    },
    {
        "id": "M10",
        "name": "store_allows_duplicate_rev",
        "file": "massaru/store.py",
        "test": "test_duplicate_rev_fails",
        "old": """            if rev != expected_rev:
                report.errors.append(
                    f"rev mismatch: expected {expected_rev}, got {rev}"
                )
                break""",
        "new": """            if False:
                report.errors.append(
                    f"rev mismatch: expected {expected_rev}, got {rev}"
                )
                break""",
    },
    {
        "id": "M11",
        "name": "store_skips_hash_check",
        "file": "massaru/store.py",
        "test": "test_corrupt_middle_fails_fsck_and_start",
        "old": """            if digest != _txn_hash(rev, prev, txn):
                report.errors.append(f"hash mismatch at rev {rev}")
                break""",
        "new": """            if False:
                report.errors.append(f"hash mismatch at rev {rev}")
                break""",
    },
    {
        "id": "M12",
        "name": "store_skips_fsync",
        "file": "massaru/store.py",
        "test": "test_fsync_is_called_before_ack",
        "old": """                for line in lines:
                    handle.write(line)
                handle.flush()
                self._fsync_fd(handle.fileno())""",
        "new": """                for line in lines:
                    handle.write(line)
                handle.flush()""",
    },
    {
        "id": "M13",
        "name": "store_does_not_update_meta",
        "file": "massaru/store.py",
        "test": "test_restart_and_meta",
        "old": """        self._stats["fsyncs"] += 1
        self._write_meta()
        self._materialize(changed, state)""",
        "new": """        self._stats["fsyncs"] += 1
        self._materialize(changed, state)""",
    },
]


def _run_pytest(
    root: Path, test: str | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        PYTHON,
        "-m",
        "pytest",
        "tests/v1",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    if test is not None:
        command.extend(["-k", test])
    return subprocess.run(
        command,
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )


def _apply(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    occurrences = source.count(old)
    if occurrences != 1:
        raise RuntimeError(f"patch matched {occurrences} times, expected 1")
    path.write_text(source.replace(old, new), encoding="utf-8")


def main() -> int:
    print("baseline: unmutated v1 suite")
    baseline = _run_pytest(ROOT)
    if baseline.returncode != 0:
        print(baseline.stdout)
        print("BASELINE FAILED")
        return 1
    print("baseline: OK\n")

    missed: list[dict[str, str]] = []
    for mutant in MUTANTS:
        label = f"{mutant['id']} {mutant['name']}"
        with tempfile.TemporaryDirectory(prefix="nemeq-v1-mutant-") as tmp:
            root = Path(tmp)
            shutil.copytree(
                ROOT / "massaru",
                root / "massaru",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            shutil.copytree(
                ROOT / "tests",
                root / "tests",
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".pytest_cache"
                ),
            )
            try:
                _apply(root / mutant["file"], mutant["old"], mutant["new"])
            except RuntimeError as exc:
                print(f"[PATCH-ERROR] {label}: {exc}")
                missed.append(mutant)
                continue

            try:
                completed = _run_pytest(root, mutant["test"])
            except subprocess.TimeoutExpired:
                print(f"[MISSED]   {label}: pytest timed out")
                missed.append(mutant)
                continue

        caught = completed.returncode != 0
        match = re.search(
            r"FAILED\s+\S*test_[^:]*\.py::(\S+)",
            completed.stdout,
        )
        witness = match.group(1) if match else (
            "pytest rc=" + str(completed.returncode)
        )
        if caught:
            print(f"[CAUGHT]   {label} -> {witness}")
        else:
            print(f"[MISSED]   {label}: suite stayed green")
            missed.append(mutant)

    print(f"\ncaught {len(MUTANTS) - len(missed)}/{len(MUTANTS)} mutants")
    if missed:
        for mutant in missed:
            print(f"  missed {mutant['id']} {mutant['name']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
