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

def _run_pytest(root: Path, test: str) -> subprocess.CompletedProcess[str]:
    """Run one pytest target with a clean bytecode environment."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "-x", "--no-header"],
        cwd=str(root), capture_output=True, text=True,
        timeout=180, env=env, check=False,
    )


def _apply_mutant(root: Path, mutant: dict) -> str | None:
    """Apply one mutant in place. Return the original source, or None if the
    anchor text is missing (patch error)."""
    path = root / mutant["file"]
    original = path.read_text(encoding="utf-8")
    if mutant["old"] not in original:
        return None
    path.write_text(original.replace(mutant["old"], mutant["new"], 1), encoding="utf-8")
    return original


def _restore(path: Path, original: str) -> None:
    """Write back the pristine source after a mutant run."""
    path.write_text(original, encoding="utf-8")


def _witness(completed: subprocess.CompletedProcess[str]) -> str:
    """FAILED test name from pytest output, or the exit code fallback."""
    match = re.search(r"FAILED\s+(\S+)", completed.stdout)
    return match.group(1) if match else f"pytest rc={completed.returncode}"

MUTANTS: list[tuple[str, str, str, str, str, str]] = [

    ("P1", "admission_skips_readonly", "apiserver/admission.py",
     "tests/p0/test_admission.py::test_read_only_policy",
     "    if kind in FORBIDDEN_KINDS:", "    if False:"),
    ("P2", "admission_skips_rbac", "apiserver/admission.py",
     "tests/p0/test_admission.py::test_admit_rejects_rbac",
     "    if not authorize(grants, verb, s, subject=(actor or None)):", "    if False:"),
    ("P3", "admission_skips_quota", "apiserver/admission.py",
     "tests/p0/test_admission.py::test_admit_quota",
     '    quota_reason = check_quota(kind, {**spec, "_name": name}, quotas)', "    quota_reason = None"),
    ("P4", "admission_skips_leader_guard", "apiserver/admission.py",
     "tests/p0/test_admission.py::test_admit_grant_leader_change",
     '    if kind == "Grant" and prev is not None:', "    if False:"),
    ("P5", "audit_deny_not_written", "apiserver/handlers.py",
     "tests/p0/test_api_audit_watch.py::test_readonly_denied_writes_audit",
     "        self.store.put(key, payload)", "        return None"),
    ("P6", "cas_expected_rev_ignored", "apiserver/handlers.py",
     "tests/p0/test_api_cas.py::test_cas_conflict",
     "                                expected_rev=_resource_version(body))", "                                expected_rev=None)"),
    ("P7", "status_subresource_unchecked", "apiserver/handlers.py",
     "tests/p0/test_api_cas.py::test_status_subresource",
     "        if plural not in STATUS_SUBRESOURCE:", "        if False:"),
    ("P9", "routes_accept_unknown_plural", "apiserver/routes.py",
     "tests/p0/test_routes.py::test_parse_path_unknown_plural",
     "    if plural not in RESOURCES:", "    if False:"),
    ("P10", "defaults_mutate_input", "apiserver/resources.py",
     "tests/p0/test_admission.py::test_defaults_epishu",
     "    out: dict[str, Any] = copy.deepcopy(spec)", "    out: dict[str, Any] = spec"),

    ("P13", "port_env_ignored", "apiserver/server.py",
     "tests/p0/test_server.py::test_port_from_env",
     '        return int(os.environ.get("NEMEQ_API_PORT", str(DEFAULT_PORT)))', "        return DEFAULT_PORT"),
    ("P12", "envelope_drops_status", "apiserver/envelope.py",
     "tests/p0/test_envelope.py::test_to_envelope_missing_fields",
     '        envelope["status"] = {}', '        envelope["status"] = {"x": 1}'),
    ("P11", "rbac_admin_wildcard_removed", "apiserver/rbac.py",
     "tests/p0/test_rbac_ceiling.py::test_authorize_uses_role_ceiling",
     '        elif "*" in ceiling or verb in ceiling:\n            granted.add(verb)', '        elif True:\n            granted.add(verb)'),
    ("P15", "authz_allows_empty_actor", "apiserver/authz.py",
     "tests/p0/test_authz.py::test_no_actor_denied",
     "    if not actor or not actor.strip():", "    if False:"),
    ("P16", "authz_ignores_subject", "apiserver/authz.py",
     "tests/p0/test_authz.py::test_actor_without_grant_denied",
     "    return [g for g in grants if subject_of(g) == actor]", "    return list(grants)"),
    ("P17", "handlers_get_skips_authz", "apiserver/handlers.py",
     "tests/p0/test_authz.py::test_scoped_viewer_sees_only_scope",
     '        if not visible(grants, "get", kind, name, stored.get("spec", {}), actor):', "        if False:"),
    ("P20", "handlers_delete_skips_authz", "apiserver/handlers.py",
     "tests/p0/test_authz.py::test_viewer_cannot_write",
     '        if not visible(self._grants(), "delete", kind, name, spec, actor):', "        if False:"),
    ("P21", "status_uses_update_verb", "apiserver/handlers.py",
     "tests/p0/test_authz.py::test_ops_cannot_set_status_without_status_verb",
     '        if not visible(self._grants(), STATUS_VERB, kind, name, old.get("spec", {}), actor):', '        if not visible(self._grants(), "update", kind, name, old.get("spec", {}), actor):'),
    ("P22", "audit_deny_not_persisted", "apiserver/handlers.py",
     "tests/p0/test_authz.py::test_denied_operation_creates_audit",
     "        self.store.put(key, payload)", "        return None"),

    ("P23", "watchpage_plural_filter_disabled", "apiserver/watchpage.py",
     "tests/p0/test_watch_page.py::test_cross_plural_advances_watermark",
     '        group = [e for e in events[index:group_end] if str(e.get("key", "")).startswith(prefix)]', '        group = list(events[index:group_end])'),
    ("P24", "rbac_ceiling_disabled", "apiserver/rbac.py",
     "tests/p0/test_rbac_ceiling.py::test_authorize_uses_role_ceiling",
     '    if role not in VERBS:\n        return None\n    ceiling = VERBS[role]', '    if role not in VERBS:\n        return None\n    ceiling = frozenset({"*"})'),
    ("P25", "status_verb_open_to_viewer", "apiserver/rbac.py",
     "tests/p0/test_rbac_ceiling.py::test_authorize_status_only_explicit_for_ops",
     'STATUS_ROLES: frozenset[str] = frozenset({"ops", "admin_epishu", "admin"})', 'STATUS_ROLES: frozenset[str] = frozenset({"viewer", "ops", "admin_epishu", "admin"})'),
    ("P26", "collection_gate_ignores_verb", "apiserver/authz.py",
     "tests/p0/test_collection_gate.py::test_get_only_actor_cannot_list",
     "    return any(grant_verb_allows(g, verb) for g in _grants_for(grants, actor))", "    return bool(_grants_for(grants, actor))"),
    ("P27", "watchpage_plural_filter_disabled", "apiserver/watchpage.py",
     "tests/p0/test_watch_page.py::test_cross_plural_advances_watermark",
     '        group = [e for e in events[index:group_end] if str(e.get("key", "")).startswith(prefix)]', '        group = list(events[index:group_end])'),
    ("P28", "watchpage_splits_rev_group", "apiserver/watchpage.py",
     "tests/p0/test_watch_page.py::test_rev_group_overflow_keeps_group_atomic",
     '        if items and len(items) + len(eligible) > limit:\n            break', '        if items and len(items) + len(eligible) > limit:\n            eligible = eligible[: max(0, limit - len(items))]'),
    ("P29", "watchpage_deleted_hides_previous_value", "apiserver/watchpage.py",
     "tests/p0/test_watch_page.py::test_session_deleted_uses_internal_epishu_ref",
     '    value = event.get("value")\n    if isinstance(value, dict):', '    value = event.get("value") if event.get("type") != "DELETED" else None\n    if isinstance(value, dict):'),
    ("P30", "yaml_loader_unsafe", "nqctl/manifest.py",
     "tests/p0/test_manifest.py::test_rejects_python_object_tag",
     "        return yaml.load(text, Loader=yaml.SafeLoader)  # type: ignore[arg-type]", "        return yaml.load(text, Loader=yaml.FullLoader)  # type: ignore[arg-type]"),

    ("P31", "routes_ignore_fromrev_alias", "apiserver/routes.py",
     "tests/p0/test_routes_watch.py::test_from_rev_alias",
     '    from_key = "fromRev" if "fromRev" in query else "from_rev"', '    from_key = "from_rev"'),
    ("P32", "pagination_filters_after_paging", "apiserver/pagination.py",
     "tests/p0/test_authz.py::test_list_filters_before_pagination",
     '    limit, offset = normalize_page(limit, continue_token)\n    visible_items = filter_items(grants, kind, items, actor)\n    return paginate(visible_items, limit, offset)', '    limit, offset = normalize_page(limit, continue_token)\n    page, token = paginate(items, limit, offset)\n    return filter_items(grants, kind, page, actor), token'),
    ("P34", "watchgone_mapped_to_409", "apiserver/errors.py",
     "tests/p0/test_routes_watch.py::test_watch_overflow_returns_410",
     '    "WatchGone": 410,', '    "WatchGone": 409,'),
    ("P35", "openapi_fromrev_alias_removed", "apiserver/openapi.py",
     "tests/p0/test_openapi_watch.py::test_openapi_watch_contract",
     '            {"name": "from_rev", "in": "query",', '            {"name": "from_rev_disabled", "in": "query",'),
    ("P36", "openapi_watch_metadata_nextrev_removed", "apiserver/openapi.py",
     "tests/p0/test_openapi_watch.py::test_openapi_watch_contract",
     '                        "required": ["nextRev", "fromRev", "limit"],', '                        "required": ["fromRev", "limit"],'),
    ("P37", "apply_ignores_yaml_path", "nqctl/cli.py",
     "tests/p0/test_cli_apply.py::test_remote_apply_reads_yaml_file_and_calls_cmd_apply",
     '            manifest = Path(args.file).read_text(encoding="utf-8")', '            manifest = ""'),
    ("P33", "server_port_zero_falls_back", "apiserver/server.py",
     "tests/p0/test_server.py::test_make_server_binds_ephemeral_port",
     "    p = DEFAULT_PORT if port is None else port", "    p = port or DEFAULT_PORT"),
    ("P38", "list_pagination_before_scope_filter", "apiserver/pagination.py",
     "tests/p0/test_rbac_pagination_watch.py::test_scoped_pagination_skips_hidden_between_pages",
     '    visible_items = filter_items(grants, kind, items, actor)\n    return paginate(visible_items, limit, offset)', '    page, token = paginate(items, limit, offset)\n    return filter_items(grants, kind, page, actor), token'),
    ("P39", "watch_deleted_scope_uses_scope_name", "apiserver/watchpage.py",
     "tests/p0/test_rbac_pagination_watch.py::test_session_delete_scope_uses_internal_epishu_ref",
     '    value = event.get("value")\n    if isinstance(value, dict):', '    value = None\n    if isinstance(value, dict):'),
    ("P40", "list_gate_accepts_any_grant", "apiserver/authz.py",
     "tests/p0/test_rbac_pagination_watch.py::test_get_only_actor_cannot_list",
     '    return any(grant_verb_allows(g, verb) for g in _grants_for(grants, actor))', '    return bool(_grants_for(grants, actor))'),
    ("P41", "continue_token_leaks_global_count", "apiserver/pagination.py",
     "tests/p0/test_rbac_pagination_watch.py::test_hidden_objects_do_not_force_a_continue_token",
     '    return paginate(visible_items, limit, offset)', '    page, _tok = paginate(visible_items, limit, offset)\n    return page, (str(len(items)) if len(visible_items) > len(page) else None)'),
]


def main() -> int:
    """Скопировать репозиторий, прогнать baseline и каждого мутанта."""
    works = Path(tempfile.mkdtemp(prefix="nemeq-p0-mut-"))
    root = works / "repo"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache", "run", "*.pyc"))
    if not (root / "tests/p0").is_dir():
        print("tests/p0 missing")
        return 1
    baseline = _run_pytest(root, "tests/p0")
    if baseline.returncode != 0:
        print("baseline FAILED")
        print(baseline.stdout[-3000:])
        return 1
    print("baseline: OK")
    missed: list[str] = []
    for mid, name, file, test, old, new in MUTANTS:
        path = root / file
        original = _apply_mutant(root, {"file": file, "old": old, "new": new})
        if original is None:
            print(f"[PATCH-ERROR] {mid} {name}")
            missed.append(mid)
            continue
        try:
            completed = _run_pytest(root, test)
            caught = completed.returncode != 0
            witness = _witness(completed)
        finally:
            _restore(path, original)
        label = f"{mid} {name}"
        if caught:
            print(f"[CAUGHT]   {label} -> {witness}")
        else:
            print(f"[MISSED]   {label}: suite stayed green")
            missed.append(mid)
    print(f"\ncaught {len(MUTANTS) - len(missed)}/{len(MUTANTS)} mutants")
    shutil.rmtree(works, ignore_errors=True)
    return 1 if missed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
