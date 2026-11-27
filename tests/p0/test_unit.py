"""Тесты генерации systemd-юнита (v0.6 §6.5, мутанты M-6.5-*)."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from massaru.layout import resolve_all
from massaru.unit import (
    UnitError,
    build_unit,
    unit_name,
    verify_unit,
    write_unit,
)


def resolved_for(tmp_path, instance="alpha"):
    return resolve_all(home=str(tmp_path / "home"), instance=instance, env={})


class TestNaming:
    def test_unit_name_contains_instance(self, tmp_path):
        # M-6.5-a: имя юнита без инстанса — два инстанса конфликтуют
        assert unit_name("alpha") == "nemeq-alpha.service"
        assert unit_name("beta") == "nemeq-beta.service"
        assert unit_name("alpha") != unit_name("beta")


class TestSpecifiers:
    def test_no_percent_h(self, tmp_path):
        # M-6.5-b: %h в юните — тест падает
        r = resolved_for(tmp_path)
        spec = build_unit(r)
        assert "%h" not in spec.content
        for ch in ("%h", "%u", "%U", "%s", "%S", "%t"):
            assert ch not in spec.content

    def test_reject_specifier_injection(self, tmp_path):
        """Если генератор получит путь с %h, юнит не должен молча собраться.

        Генератор отказывает раньше, чем юнит создан — это сильнее, чем
        проверка постфактум через verify_unit.
        """
        r = resolve_all(home="/tmp/%h/home", instance="alpha", env={})
        with pytest.raises(UnitError) as exc:
            build_unit(r)
        assert exc.value.code == 2
        assert "%h" in exc.value.message


class TestPaths:
    def test_all_paths_absolute(self, tmp_path):
        # M-6.5-c: относительный путь в юните — тест падает
        r = resolved_for(tmp_path)
        spec = build_unit(r)
        for line in spec.content.splitlines():
            for key in ("Environment=NEMEQ_HOME=", "ReadWritePaths=", "ReadOnlyPaths="):
                if line.startswith(key):
                    val = line[len(key):]
                    assert val.startswith("/"), f"not absolute: {line}"
        assert "ExecStart=/" in spec.content

    def test_readwrite_narrowed(self, tmp_path):
        """ReadWritePaths только var/logs/run, не весь префикс."""
        # M-6.5-d: ReadWritePaths на весь префикс — тест падает
        r = resolved_for(tmp_path)
        spec = build_unit(r)
        rw = [l.split("=", 1)[1] for l in spec.content.splitlines()
              if l.startswith("ReadWritePaths=")]
        assert len(rw) == 3
        for p in rw:
            assert p.rstrip("/") != str(r.home), "весь префикс не должен быть writable"
            assert str(r.home) in p
        assert any(p.endswith("/var") for p in rw)
        assert any(p.endswith("/logs") for p in rw)
        assert any(p.endswith("/run") for p in rw)

    def test_lib_readonly(self, tmp_path):
        # M-6.5-e: lib не помечен ReadOnlyPaths — тест падает
        r = resolved_for(tmp_path)
        spec = build_unit(r)
        assert "ReadOnlyPaths=" in spec.content
        ro = [l.split("=", 1)[1] for l in spec.content.splitlines()
              if l.startswith("ReadOnlyPaths=")]
        assert any(p.endswith("/lib") for p in ro)

    def test_verify_detects_writable_lib(self, tmp_path):
        r = resolved_for(tmp_path)
        spec = build_unit(r)
        broken = spec.content.replace(
            f"ReadOnlyPaths={r.home}/lib",
            f"ReadWritePaths={r.home}/lib",
        )
        spec.content = broken
        check = verify_unit(spec)
        assert check["ok"] is False


class TestModes:
    def test_user_unit_by_default(self, tmp_path):
        # M-6.5-f: юнит всегда системный — установка без root ломается
        r = resolved_for(tmp_path)
        spec = build_unit(r, user=str(tmp_path / "userhome"))
        assert ".config/systemd/user" in str(spec.path)
        assert spec.system is False

    def test_system_unit_explicit(self, tmp_path):
        r = resolved_for(tmp_path)
        spec = build_unit(r, system=True)
        assert str(spec.path).startswith("/etc/systemd/system/")
        assert spec.system is True

    def test_user_unit_has_default_target(self, tmp_path):
        r = resolved_for(tmp_path)
        user_spec = build_unit(r, user=str(tmp_path / "h"))
        assert "WantedBy=default.target" in user_spec.content
        sys_spec = build_unit(r, system=True)
        assert "WantedBy=multi-user.target" in sys_spec.content


class TestWrite:
    def test_write_creates_file(self, tmp_path):
        r = resolved_for(tmp_path)
        spec = build_unit(r, user=str(tmp_path / "userhome"))
        path = write_unit(spec)
        assert path.is_file()
        assert "ExecStart=" in path.read_text()

    def test_dir_permission_0700(self, tmp_path):
        r = resolved_for(tmp_path)
        spec = build_unit(r, user=str(tmp_path / "userhome"))
        write_unit(spec)
        mode = stat.S_IMODE(os.stat(spec.path.parent).st_mode)
        assert mode == 0o700

    def test_file_permission_0644(self, tmp_path):
        r = resolved_for(tmp_path)
        spec = build_unit(r, user=str(tmp_path / "userhome"))
        write_unit(spec)
        mode = stat.S_IMODE(os.stat(spec.path).st_mode)
        assert mode == 0o644


class TestHardening:
    def test_sandbox_keys_present(self, tmp_path):
        # M-6.5-g: убрать hardening — тест падает
        r = resolved_for(tmp_path)
        spec = build_unit(r)
        for key in ("NoNewPrivileges=yes", "PrivateTmp=yes",
                    "ProtectSystem=strict", "ProtectHome=read-only"):
            assert key in spec.content, f"missing {key}"

    def test_no_bytecode_env(self, tmp_path):
        # M-4.3-a: не выставить PYTHONDONTWRITEBYTECODE — lib станет записываемым
        r = resolved_for(tmp_path)
        spec = build_unit(r)
        assert "Environment=PYTHONDONTWRITEBYTECODE=1" in spec.content
