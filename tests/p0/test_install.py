"""Тесты install и preflight (v0.6 §2.1, §4.3–4.7, §5, мутанты M-2.x, M-4.3–4.7)."""
from __future__ import annotations

import json
import os
import platform
import stat
import sys
from pathlib import Path

import pytest

from massaru import install as inst
from massaru.install import (
    InstallError,
    check_schema_compat,
    preflight,
    read_manifest,
)
from massaru.layout import resolve_all


def resolved_for(tmp_path, instance="alpha"):
    return resolve_all(home=str(tmp_path / "home"), instance=instance, env={})


class TestPreflight:
    def test_all_checks_present(self, tmp_path):
        r = resolved_for(tmp_path)
        pf = preflight(r)
        names = {c["check"] for c in pf.checks}
        assert {"python", "arch", "glibc", "filesystem", "disk_space"} <= names

    def test_current_host_passes_python_and_arch(self, tmp_path):
        r = resolved_for(tmp_path)
        pf = preflight(r)
        by = {c["check"]: c for c in pf.checks}
        vi = sys.version_info
        assert by["python"]["ok"] == ((vi.major, vi.minor) >= (3, 12))
        assert by["arch"]["ok"] == (platform.machine() == "x86_64")

    def test_glibc_too_high_rejected(self, tmp_path):
        # M-2.1-a: не проверять min_glibc — тест падает
        r = resolved_for(tmp_path)
        pf = preflight(r, min_glibc="99.0")
        by = {c["check"]: c for c in pf.checks}
        assert by["glibc"]["ok"] is False
        assert pf.ok is False

    def test_no_directories_created_on_failed_preflight(self, tmp_path):
        """Главное требование §2.1б: отказ ДО создания каталогов."""
        r = resolved_for(tmp_path)
        before = set(tmp_path.iterdir())
        with pytest.raises(InstallError) as exc:
            inst.install(r, min_glibc="99.0")
        assert exc.value.code == 2
        assert set(tmp_path.iterdir()) == before, "каталоги не должны создаваться"
        assert not r.home.exists()

    def test_disk_space_check(self, tmp_path):
        r = resolved_for(tmp_path)
        pf = preflight(r)
        by = {c["check"]: c for c in pf.checks}
        assert "disk_space" in by
        assert by["disk_space"]["ok"] is True

    def test_socket_path_length_checked(self):
        # M-4.6-a: не проверять длину пути — тест падает
        long_home = "/tmp/" + "a" * 120
        r = resolve_all(home=long_home, instance="alpha", env={})
        pf = preflight(r)
        by = {c["check"]: c for c in pf.checks}
        assert by["socket_path_len"]["ok"] is False


class TestInstall:
    def test_install_creates_manifest(self, tmp_path):
        r = resolved_for(tmp_path)
        out = inst.install(r)
        manifest = read_manifest(r.home)
        assert manifest["layout_version"] == 6
        assert manifest["schema_version"] == 4
        assert out["installed"]["instanceId"]

    def test_manifest_permissions_0600(self, tmp_path):
        r = resolved_for(tmp_path)
        inst.install(r)
        m = r.home / "lib" / "INSTALL.json"
        assert stat.S_IMODE(os.stat(m).st_mode) == 0o600

    def test_install_idempotent_identity(self, tmp_path):
        # M-6.3-d: повторный install меняет home_id — тест падает
        r = resolved_for(tmp_path)
        first = inst.install(r)["installed"]
        second = inst.install(r)["installed"]
        assert first["homeId"] == second["homeId"]
        assert first["instanceId"] == second["instanceId"]
        assert first["storeId"] == second["storeId"]


class TestSchemaCompat:
    def test_newer_store_schema_rejected(self, tmp_path):
        # M-4.5-a: не сверять schema_version — тест падает
        r = resolved_for(tmp_path)
        inst.install(r, schema_version=4)
        check_schema_compat(r.home, 4)  # ровно то же — ок
        with pytest.raises(InstallError) as exc:
            check_schema_compat(r.home, 5)
        assert "schema 5 > supported 4" in str(exc.value)
