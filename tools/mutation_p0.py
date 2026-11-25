#!/usr/bin/env python3
"""Mutation gate for apiserver P0 (admission/CAS/watch/AUDIT_DENY/status)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

MUTANTS = [
    {
        "id": "P1",
        "name": "admission_skips_readonly",
        "file": "apiserver/admission.py",
        "test": "tests/p0/test_admission.py::test_read_only_policy",
        "old": '    if kind in FORBIDDEN_KINDS:',
        "new": '    if False:',
    },
    {
        "id": "P2",
        "name": "admission_skips_rbac",
        "file": "apiserver/admission.py",
        "test": "tests/p0/test_admission.py::test_admit_rejects_rbac",
        "old": '    if not authorize(grants, verb, s, subject=(actor or None)):',
        "new": '    if False:',
    },
    {
        "id": "P3",
        "name": "admission_skips_quota",
        "file": "apiserver/admission.py",
        "test": "tests/p0/test_admission.py::test_admit_quota",
        "old": '    quota_reason = check_quota(kind, {**spec, "_name": name}, quotas)',
        "new": '    quota_reason = None',
    },
    {
        "id": "P4",
        "name": "admission_skips_leader_guard",
        "file": "apiserver/admission.py",
        "test": "tests/p0/test_admission.py::test_admit_grant_leader_change",
        "old": '    if kind == "Grant" and prev is not None:',
        "new": '    if False:',
    },
    {
        "id": "P5",
        "name": "audit_deny_not_written",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_api_audit_watch.py::test_readonly_denied_writes_audit",
        "old": '        self.store.put(key, payload)',
        "new": '        return None',
    },
    {
        "id": "P6",
        "name": "cas_expected_rev_ignored",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_api_cas.py::test_cas_conflict",
        "old": '        rv = _resource_version(body)',
        "new": '        rv = None',
    },
    {
        "id": "P7",
        "name": "status_subresource_unchecked",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_api_cas.py::test_status_subresource",
        "old": '        if plural not in STATUS_SUBRESOURCE:',
        "new": '        if False:',
    },
    {
        "id": "P8",
        "name": "watch_filter_disabled",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_api_audit_watch.py::test_watch_filters_by_kind",
        "old": '            if not evt.get("key", "").startswith(f"{plural}/"):',
        "new": '            if False:',
    },
    {
        "id": "P9",
        "name": "routes_accept_unknown_plural",
        "file": "apiserver/routes.py",
        "test": "tests/p0/test_routes.py::test_parse_path_unknown_plural",
        "old": '    if plural not in RESOURCES:',
        "new": '    if False:',
    },
    {
        "id": "P13",
        "name": "port_env_ignored",
        "file": "apiserver/server.py",
        "test": "tests/p0/test_server.py::test_port_from_env",
        "old": '        return int(os.environ.get("NEMEQ_API_PORT", str(DEFAULT_PORT)))',
        "new": '        return DEFAULT_PORT',
    },
    {
        "id": "P14",
        "name": "pagination_token_ignored",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_server.py::test_handlers_list_pagination",
        "old": '        items, nxt = self.store.list(f"{plural}/", limit=limit, continue_token=continue_token)',
        "new": '        items, nxt = self.store.list(f"{plural}/", limit=limit)',
    },
    {
        "id": "P12",
        "name": "envelope_drops_status",
        "file": "apiserver/envelope.py",
        "test": "tests/p0/test_envelope.py::test_to_envelope_missing_fields",
        "old": '        envelope["status"] = {}',
        "new": '        envelope["status"] = {"x": 1}',
    },
    {
        "id": "P11",
        "name": "rbac_admin_wildcard_removed",
        "file": "apiserver/rbac.py",
        "test": "tests/p0/test_rbac.py::test_grant_allows_roles",
        "old": '    if "*" in role_verbs or "*" in user_verbs:',
        "new": '    if "*" in user_verbs:',
    },
    {
        "id": "P10",
        "name": "defaults_mutate_input",
        "file": "apiserver/resources.py",
        "test": "tests/p0/test_admission.py::test_defaults_epishu",
        "old": '    out: dict[str, Any] = copy.deepcopy(spec)',
        "new": '    out: dict[str, Any] = spec',
    },
]


def _run_pytest(root: Path, test: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [PYTHON, "-m", "pytest", test, "-q", "-x", "--no-header"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        check=False,
    )


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="nemeq-p0-mut-"))
    root = work / "repo"
    shutil.copytree(
        ROOT,
        root,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", "run", "*.pyc"
        ),
    )
    baseline = _run_pytest(root, "tests/p0")
    if baseline.returncode != 0:
        print("baseline FAILED")
        print(baseline.stdout[-3000:])
        return 1
    print("baseline: OK")
    missed = []
    for mutant in MUTANTS:
        path = root / mutant["file"]
        original = path.read_text(encoding="utf-8")
        if mutant["old"] not in original:
            print(f"[PATCH-ERROR] {mutant['id']} {mutant['name']}")
            missed.append(mutant)
            continue
        path.write_text(
            original.replace(mutant["old"], mutant["new"], 1), encoding="utf-8"
        )
        try:
            completed = _run_pytest(root, mutant["test"])
            caught = completed.returncode != 0
            match = re.search(r"FAILED\s+(\S+)", completed.stdout)
            witness = match.group(1) if match else f"pytest rc={completed.returncode}"
        finally:
            path.write_text(original, encoding="utf-8")
        label = f"{mutant['id']} {mutant['name']}"
        if caught:
            print(f"[CAUGHT]   {label} -> {witness}")
        else:
            print(f"[MISSED]   {label}: suite stayed green")
            missed.append(mutant)
    print(f"\ncaught {len(MUTANTS) - len(missed)}/{len(MUTANTS)} mutants")
    shutil.rmtree(work, ignore_errors=True)
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
