#!/usr/bin/env python3
"""Mutation run for the wave-2 runtime agents.

The five mutants below are exactly the minimum from wave2_brief_20261113.md:
init skips a child, reu does not restart, capd trusts its argument, adannu
ignores the schedule, adannu turns context overflow into sleep.  Each mutant
is run against the single acceptance test that covers its criterion.
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
AGENTS = ROOT / "agents"
PYTHON = sys.executable

MUTANTS: list[dict[str, str]] = [
    {
        "id": "M1",
        "criterion": "1",
        "name": "init_does_not_start_capd",
        "file": "agents/init.py",
        "test": "test_init_starts_three_agents",
        "old": """        for name, spec in manifest["agents"].items():
            command = agent_command(spec, manifest_path.parent)""",
        "new": """        for name, spec in manifest["agents"].items():
            if name == "capd":
                continue
            command = agent_command(spec, manifest_path.parent)""",
    },
    {
        "id": "M2",
        "criterion": "2",
        "name": "reu_does_not_restart",
        "file": "agents/reu.py",
        "test": "test_reu_restarts_adannu_after_sigkill",
        "old": """        record["restarts"] = int(record["restarts"]) + 1""",
        "new": """        record["restarts"] = int(record["restarts"]) + 0
        continue""",
    },
    {
        "id": "M3",
        "criterion": "3",
        "name": "capd_checks_argument_not_registry",
        "file": "agents/capd.py",
        "test": "test_capd_denies_without_grant_and_allows_after_grant",
        "old": """    return capability in state.get("grants", {}).get(agent, [])""",
        "new": """    return bool(capability)""",
    },
    {
        "id": "M4",
        "criterion": "4",
        "name": "adannu_ignores_schedule",
        "file": "agents/adannu.py",
        "test": "test_adannu_schedule_fires_sleep_requested",
        "old": """            if now >= next_monotonic:""",
        "new": """            if False:""",
    },
    {
        "id": "M5",
        "criterion": "5",
        "name": "adannu_sleeps_on_context_overflow",
        "file": "agents/adannu.py",
        "test": "test_context_overflow_does_not_emit_sleep_requested",
        "old": """            if consume_overflow(run_dir, state):
                write_status(run_dir, state)""",
        "new": """            if consume_overflow(run_dir, state):
                append_event(
                    run_dir / EVENTS_NAME,
                    {
                        "event": config["event"],
                        "timestamp": time.time(),
                        "source": "overflow",
                    },
                )
                write_status(run_dir, state)""",
    },
]


def _run_pytest(root: Path, test: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            PYTHON,
            "-m",
            "pytest",
            "agents/tests/test_agents.py",
            "-q",
            "-k",
            test,
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
    )


def _apply(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    occurrences = source.count(old)
    if occurrences != 1:
        raise RuntimeError(f"patch matched {occurrences} times, expected 1")
    path.write_text(source.replace(old, new), encoding="utf-8")


def main() -> int:
    print("baseline: unmutated agents suite")
    baseline = subprocess.run(
        [
            PYTHON,
            "-m",
            "pytest",
            "agents/tests",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if baseline.returncode != 0:
        print(baseline.stdout)
        print("BASELINE FAILED")
        return 1
    print("baseline: OK\n")

    missed: list[dict[str, str]] = []
    for mutant in MUTANTS:
        label = (
            f"{mutant['id']} criterion {mutant['criterion']} "
            f"{mutant['name']}"
        )
        with tempfile.TemporaryDirectory(prefix="nemeq-wave2-mutant-") as tmp:
            root = Path(tmp)
            shutil.copytree(
                AGENTS,
                root / "agents",
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".pytest_cache"
                ),
            )
            shutil.copy2(ROOT / "riksu.toml", root / "riksu.toml")
            try:
                _apply(root / mutant["file"], mutant["old"], mutant["new"])
                compile(
                    (root / mutant["file"]).read_text(encoding="utf-8"),
                    str(root / mutant["file"]),
                    "exec",
                )
            except (RuntimeError, SyntaxError) as exc:
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
            r"FAILED\s+\S*test_agents\.py::(test_\w+)", completed.stdout
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
