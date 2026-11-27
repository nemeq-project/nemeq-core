"""Тесты doctor (v0.6 §8.5–8.8, мутанты M-8.x)."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from massaru.doctor import (
    DEFAULT_MAX_STATE_BYTES,
    run_doctor,
)
from massaru.install import install
from massaru.layout import resolve_all


def installed(tmp_path, instance="alpha"):
    r = resolve_all(home=str(tmp_path / "home"), instance=instance, env={})
    install(r)
    return r


class _Skip(Exception):
    pass


def setUp_ok(tmp_path):
    try:
        return installed(tmp_path)
    except Exception as exc:  # preflight на неподходящем хосте
        pytest.skip(f"install недоступен: {exc}")


class TestBasics:
    def test_missing_home_fails(self, tmp_path):
        # M-8.5-a: отсутствие HOME не диагностируется — тест падает
        r = resolve_all(home=str(tmp_path / "nope"), instance="alpha", env={})
        rep = run_doctor(r)
        assert rep.ok is False
        assert any(f.check == "home:exists" and not f.ok for f in rep.findings)

    def test_report_is_json_serialisable(self, tmp_path):
        # M-8.8-a: отчёт не машинно-читаемый — тест падает
        r = setUp_ok(tmp_path)
        rep = run_doctor(r)
        text = json.dumps(rep.as_dict(), ensure_ascii=False)
        parsed = json.loads(text)
        assert "findings" in parsed
        assert isinstance(parsed["findings"], list)
        assert all({"check", "ok", "detail", "severity"} <= set(f) for f in parsed["findings"])


class TestPermissions:
    def test_var_mode_checked(self, tmp_path):
        # M-4.7-a: права var не проверяются — тест падает
        r = setUp_ok(tmp_path)
        os.chmod(r.var, 0o755)
        rep = run_doctor(r)
        assert any(f.check == "perm:var" and not f.ok for f in rep.findings)

    def test_owner_checked(self, tmp_path, monkeypatch):
        """M-4.7-b: чужой UID обнаруживается.

        Раньше тест скипался без root и был вакуумным ровно в обязательном
        режиме §9.8. Здесь владелец подменяется в `os.lstat`: проверяется сама
        логика сравнения, а не право делать `chown`.
        """
        r = setUp_ok(tmp_path)
        victim = r.var / "store"
        real = os.lstat
        victim_path = str(victim)

        def fake(path, *a, **kw):
            st = real(path, *a, **kw)
            # Сравнение БЕЗ realpath: realpath сам вызывает lstat, и подмена
            # ушла бы в бесконечную рекурсию.
            if str(path) == victim_path:
                values = list(st)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return st

        monkeypatch.setattr(os, "lstat", fake)
        rep = run_doctor(r)
        assert any(f.check == "perm:owner" and not f.ok for f in rep.findings), (
            "чужой владелец не обнаружен"
        )


class TestFilesystems:
    def test_same_device_ok(self, tmp_path):
        r = setUp_ok(tmp_path)
        rep = run_doctor(r)
        assert any(f.check == "fs:same_device" for f in rep.findings)

    def test_cross_device_tmp_flagged(self, tmp_path, monkeypatch):
        """M-4.4-a: var/tmp на другой ФС → doctor падает.

        Смонтировать отдельную ФС в контейнере нельзя (нет CAP_SYS_ADMIN),
        поэтому расхождение st_dev вводится подменой `_st_dev` — проверяется
        сама логика сравнения, а не право монтировать.
        """
        import massaru.doctor as doc

        r = setUp_ok(tmp_path)
        tmp_dir = r.var / "tmp"
        epishu = r.var / "epishu"
        tmp_path_real = str(tmp_dir)

        def fake_st_dev(path):
            if str(path) == tmp_path_real:
                return 900001  # «другая ФС»
            return 900000

        monkeypatch.setattr(doc, "_st_dev", fake_st_dev)
        rep = run_doctor(r)
        finding = next(f for f in rep.findings if f.check == "fs:same_device")
        assert not finding.ok, "расхождение устройств не обнаружено"
        assert rep.ok is False

    def test_same_device_not_flagged(self, tmp_path, monkeypatch):
        """Обратная сторона: одинаковые st_dev не дают ложного отказа."""
        import massaru.doctor as doc

        r = setUp_ok(tmp_path)
        monkeypatch.setattr(doc, "_st_dev", lambda path: 900000)
        rep = run_doctor(r)
        finding = next(f for f in rep.findings if f.check == "fs:same_device")
        assert finding.ok, f"ложный отказ: {finding.detail}"


class TestSocketPath:
    def test_long_path_flagged(self, tmp_path):
        # M-4.6-a: длина пути не проверяется — тест падает
        r = resolve_all(home="/tmp/" + "x" * 120, instance="alpha", env={})
        rep = run_doctor(r)
        # home не существует -> будет home:exists, но проверку длины всё равно ждём
        r.home.mkdir(parents=True, exist_ok=True)
        rep = run_doctor(r)
        assert any(f.check == "path:socket_len" and not f.ok for f in rep.findings)


class TestDisk:
    def test_insufficient_space_flagged(self, tmp_path):
        # M-4.7-c: место не проверяется — тест падает
        r = setUp_ok(tmp_path)
        rep = run_doctor(r, max_state_bytes=10 ** 15)  # требуем петабайты
        assert any(f.check == "disk:space" and not f.ok for f in rep.findings)


class TestManifest:
    def test_manifest_present(self, tmp_path):
        r = setUp_ok(tmp_path)
        rep = run_doctor(r)
        assert any(f.check == "manifest:present" and f.ok for f in rep.findings)

    def test_missing_manifest_flagged(self, tmp_path):
        r = setUp_ok(tmp_path)
        (r.home / "lib" / "INSTALL.json").unlink()
        rep = run_doctor(r)
        assert any(f.check == "manifest:present" and not f.ok for f in rep.findings)


class TestSandbox:
    def test_sandbox_reported_as_warning(self, tmp_path):
        """Песочница — best-effort: отсутствие не должно делать отчёт красным."""
        r = setUp_ok(tmp_path)
        rep = run_doctor(r)
        sb = [f for f in rep.findings if f.check.startswith("sandbox:")]
        assert sb, "sandbox-проверки должны присутствовать"
        assert all(f.severity == "warning" for f in sb)

    def test_hardened_required_turns_red(self, tmp_path):
        # M-6.5-i: требование hardened игнорируется — тест падает
        r = setUp_ok(tmp_path)
        rep = run_doctor(r, hardened_required=True)
        if rep.hardened:
            pytest.skip("песочница действительно применена")
        assert any(f.check == "sandbox:required" and not f.ok for f in rep.findings)
        assert rep.ok is False


class TestLock:
    def test_lock_inside_locks_dir(self, tmp_path):
        r = setUp_ok(tmp_path)
        rep = run_doctor(r)
        assert any(f.check == "lock:location" and f.ok for f in rep.findings)

    def test_lock_outside_locks_dir_flagged(self, tmp_path, monkeypatch):
        """M-6.4-a: если lock_path вернёт путь вне var/locks, doctor обязан упасть.

        Подменяем lock_path в модуле doctor — иначе штатная формула всегда даёт
        locks/, и проверка была бы вакуумной.
        """
        import massaru.doctor as doc

        r = setUp_ok(tmp_path)
        monkeypatch.setattr(doc, "lock_path",
                            lambda var, store, home=None: var / "store" / "lock")
        rep = doc.run_doctor(r)
        assert any(f.check == "lock:location" and not f.ok for f in rep.findings), \
            "doctor не заметил lock вне var/locks"

    def test_symlink_target_checked(self, tmp_path):
        # M-8.9-a: симлинк var не проверяется — тест падает
        r = setUp_ok(tmp_path)
        link = r.home / "var"
        if link.exists() and not link.is_symlink():
            pytest.skip("var уже каталог")
        wrong = r.home / "instances" / "alpha" / "var"
        link.symlink_to(wrong)
        rep = run_doctor(r)
        assert any(f.check == "symlink:var" and not f.ok for f in rep.findings)
