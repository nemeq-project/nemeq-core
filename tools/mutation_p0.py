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
        "old": '            expected_rev=_resource_version(body),',
        "new": '            expected_rev=None,',
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
        "test": "tests/p0/test_rbac_ceiling.py::test_authorize_uses_role_ceiling",
        "old": '        if "*" in ceiling or verb in ceiling:\n            granted.add(verb)',
        "new": '        if True:\n            granted.add(verb)',
    },
    {
        "id": "P10",
        "name": "defaults_mutate_input",
        "file": "apiserver/resources.py",
        "test": "tests/p0/test_admission.py::test_defaults_epishu",
        "old": '    out: dict[str, Any] = copy.deepcopy(spec)',
        "new": '    out: dict[str, Any] = spec',
    },
    {
        "id": "P15",
        "name": "authz_allows_empty_actor",
        "file": "apiserver/authz.py",
        "test": "tests/p0/test_authz.py::test_no_actor_denied",
        "old": '    if not actor or not actor.strip():',
        "new": '    if False:',
    },
    {
        "id": "P16",
        "name": "authz_ignores_subject",
        "file": "apiserver/authz.py",
        "test": "tests/p0/test_authz.py::test_actor_without_grant_denied",
        "old": '    return [g for g in grants if subject_of(g) == actor]',
        "new": '    return list(grants)',
    },
    {
        "id": "P17",
        "name": "handlers_get_skips_authz",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_authz.py::test_scoped_viewer_sees_only_scope",
        "old": '        if not visible(self._grants(), "get", kind, name, stored.get("spec", {}), actor):',
        "new": '        if False:',
    },
    {
        "id": "P20",
        "name": "handlers_delete_skips_authz",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_authz.py::test_viewer_cannot_write",
        "old": '        if not visible(self._grants(), "delete", kind, name, spec, actor):',
        "new": '        if False:',
    },
    {
        "id": "P21",
        "name": "status_uses_update_verb",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_authz.py::test_ops_cannot_set_status_without_status_verb",
        "old": '        if not visible(self._grants(), STATUS_VERB, kind, name, old.get("spec", {}), actor):',
        "new": '        if not visible(self._grants(), "update", kind, name, old.get("spec", {}), actor):',
    },
    {
        "id": "P22",
        "name": "audit_deny_not_persisted",
        "file": "apiserver/handlers.py",
        "test": "tests/p0/test_authz.py::test_denied_operation_creates_audit",
        "old": '        self.store.put(key, payload)',
        "new": '        return None',
    },
    {
        "id": "P23",
        "name": "watchpage_plural_filter_disabled",
        "file": "apiserver/watchpage.py",
        "test": "tests/p0/test_watch_page.py::test_cross_plural_advances_watermark",
        "old": '        group = [\n            event for event in events[index:group_end]\n            if str(event.get("key", "")).startswith(prefix)\n        ]',
        "new": '        group = list(events[index:group_end])',
    },
    {
        "id": "P24",
        "name": "rbac_ceiling_disabled",
        "file": "apiserver/rbac.py",
        "test": "tests/p0/test_rbac_ceiling.py::test_authorize_uses_role_ceiling",
        "old": '    if role not in VERBS:\n        return None\n    ceiling = VERBS[role]',
        "new": '    if role not in VERBS:\n        return None\n    ceiling = frozenset({"*"})',
    },
    {
        "id": "P25",
        "name": "status_verb_open_to_viewer",
        "file": "apiserver/rbac.py",
        "test": "tests/p0/test_rbac_ceiling.py::test_authorize_status_only_explicit_for_ops",
        "old": 'STATUS_ROLES: frozenset[str] = frozenset({"ops", "admin_epishu", "admin"})',
        "new": 'STATUS_ROLES: frozenset[str] = frozenset({"viewer", "ops", "admin_epishu", "admin"})',
    },
    {
        "id": "P26",
        "name": "collection_gate_ignores_verb",
        "file": "apiserver/authz.py",
        "test": "tests/p0/test_collection_gate.py::test_get_only_actor_cannot_list",
        "old": '    return any(grant_verb_allows(g, verb) for g in _grants_for(grants, actor))',
        "new": '    return bool(_grants_for(grants, actor))',
    },
    {
        "id": "P27",
        "name": "watchpage_plural_filter_disabled",
        "file": "apiserver/watchpage.py",
        "test": "tests/p0/test_watch_page.py::test_cross_plural_advances_watermark",
        "old": '        group = [\n            event for event in events[index:group_end]\n            if str(event.get("key", "")).startswith(prefix)\n        ]',
        "new": '        group = list(events[index:group_end])',
    },
    {
        "id": "P28",
        "name": "watchpage_splits_rev_group",
        "file": "apiserver/watchpage.py",
        "test": "tests/p0/test_watch_page.py::test_rev_group_overflow_keeps_group_atomic",
        "old": '        if items and len(items) + len(eligible) > limit:\n            break',
        "new": '        if items and len(items) + len(eligible) > limit:\n            eligible = eligible[: max(0, limit - len(items))]',
    },
    {
        "id": "P29",
        "name": "watchpage_deleted_hides_previous_value",
        "file": "apiserver/watchpage.py",
        "test": "tests/p0/test_watch_page.py::test_session_deleted_uses_internal_epishu_ref",
        "old": '    value = event.get("value")\n    if isinstance(value, dict):',
        "new": '    value = event.get("value") if event.get("type") != "DELETED" else None\n    if isinstance(value, dict):',
    },
    {
        "id": "P30",
        "name": "yaml_loader_unsafe",
        "file": "nqctl/manifest.py",
        "test": "tests/p0/test_manifest.py::test_rejects_python_object_tag",
        "old": '        return yaml.load(text, Loader=yaml.SafeLoader)  # type: ignore[arg-type]',
        "new": '        return yaml.load(text, Loader=yaml.FullLoader)  # type: ignore[arg-type]',
    },
    {
        "id": "P31",
        "name": "routes_ignore_fromrev_alias",
        "file": "apiserver/routes.py",
        "test": "tests/p0/test_routes_watch.py::test_from_rev_alias",
        "old": '    from_key = "fromRev" if "fromRev" in query else "from_rev"',
        "new": '    from_key = "from_rev"',
    },
    {
        "id": "P32",
        "name": "pagination_filters_after_paging",
        "file": "apiserver/pagination.py",
        "test": "tests/p0/test_authz.py::test_list_filters_before_pagination",
        "old": '    limit, offset = normalize_page(limit, continue_token)\n    visible_items = filter_items(grants, kind, items, actor)\n    return paginate(visible_items, limit, offset)',
        "new": '    limit, offset = normalize_page(limit, continue_token)\n    page, token = paginate(items, limit, offset)\n    return filter_items(grants, kind, page, actor), token',
    },
    {
        "id": "P34",
        "name": "watchgone_mapped_to_409",
        "file": "apiserver/errors.py",
        "test": "tests/p0/test_routes_watch.py::test_watch_overflow_returns_410",
        "old": '    "WatchGone": 410,',
        "new": '    "WatchGone": 409,',
    },
    {
        "id": "P35",
        "name": "openapi_fromrev_alias_removed",
        "file": "apiserver/openapi.py",
        "test": "tests/p0/test_openapi_watch.py::test_openapi_watch_contract",
        "old": '            {"name": "from_rev", "in": "query",',
        "new": '            {"name": "from_rev_disabled", "in": "query",',
    },
    {
        "id": "P36",
        "name": "openapi_watch_metadata_nextrev_removed",
        "file": "apiserver/openapi.py",
        "test": "tests/p0/test_openapi_watch.py::test_openapi_watch_contract",
        "old": '                            "required": ["nextRev", "fromRev", "limit"],',
        "new": '                            "required": ["fromRev", "limit"],',
    },
    {
        "id": "P37",
        "name": "apply_ignores_yaml_path",
        "file": "nqctl/cli.py",
        "test": "tests/p0/test_cli_apply.py::test_remote_apply_reads_yaml_file_and_calls_cmd_apply",
        "old": '            manifest = Path(args.file).read_text(encoding="utf-8")',
        "new": '            manifest = ""',
    },
    {
        "id": "P33",
        "name": "server_port_zero_falls_back",
        "file": "apiserver/server.py",
        "test": "tests/p0/test_server.py::test_make_server_binds_ephemeral_port",
        "old": '    p = DEFAULT_PORT if port is None else port',
        "new": '    p = port or DEFAULT_PORT',
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
