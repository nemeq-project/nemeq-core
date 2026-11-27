"""Тесты принципов §2 (мутанты M-2.2-a, M-2.3-a, M-2.4-a)."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

from massaru import policy
from massaru.policy import (
    ALLOWED_COMMANDS,
    FORBIDDEN_COMMANDS,
    NetworkBlocked,
    PolicyError,
    check_command,
    copy_file,
    hash_file,
    network_guard,
    pip_absent,
)


class TestNoNetwork:
    def test_guard_blocks_connect(self):
        """§2.2: в запрещённом контексте любой connect падает."""
        with network_guard() as guard:
            with pytest.raises(NetworkBlocked):
                socket.create_connection(("192.0.2.1", 80), timeout=1)
        assert guard.attempts, "guard не зафиксировал попытку"

    def test_guard_restores_socket(self):
        orig = socket.socket
        with network_guard():
            assert socket.socket is not orig
        assert socket.socket is orig, "guard не восстановил socket"

    def test_install_path_makes_no_network_calls(self, tmp_path):
        """M-2.2-a: установка не должна пытаться выйти в сеть.

        Если добавить в поставку зависимость «поставить из сети», этот тест
        упадёт на попытке connect — что и требуется.
        """
        from massaru.install import install
        from massaru.layout import resolve_all

        home = tmp_path / "home"
        resolved = resolve_all(home=str(home), instance="alpha", env={})

        with network_guard() as guard:
            try:
                install(resolved)
            except NetworkBlocked:
                # Сетевая попытка — это провал теста, а не «preflight отказал».
                # Раньше здесь стоял `except Exception`, который глотал и её,
                # из-за чего мутант выживал.
                pytest.fail("установка вышла в сеть")
            except Exception:
                # preflight может отказать на неподходящем хосте — это не сеть
                pass

        assert not guard.attempts, (
            f"установка пыталась выйти в сеть: {guard.attempts[:3]}"
        )

    def test_declared_offline(self):
        """Отсутствие pip — норма, а не повод падать."""
        assert pip_absent({"PATH": "/nonexistent"}) is True


class TestAllowList:
    def test_systemctl_allowed(self):
        check_command(["systemctl", "status"])
        check_command("systemctl --user status")

    @pytest.mark.parametrize("name", sorted(FORBIDDEN_COMMANDS))
    def test_forbidden_commands_rejected(self, name):
        """M-2.3-a: замена shutil.copy2 на `cp` обязана падать."""
        with pytest.raises(PolicyError) as exc:
            check_command([name, "a", "b"])
        assert "forbidden" in exc.value.message

    def test_unknown_command_rejected(self):
        with pytest.raises(PolicyError) as exc:
            check_command(["curl", "http://x"])
        assert "allow-list" in exc.value.message

    def test_allowed_set_is_minimal(self):
        assert ALLOWED_COMMANDS == frozenset({"systemctl"})

    def test_copy_file_uses_stdlib(self, tmp_path):
        """Копирование внутрипроцессно: работает без вызова `cp`."""
        src = tmp_path / "a"
        src.write_text("data")
        dst = tmp_path / "b"
        copy_file(src, dst)
        assert dst.read_text() == "data"

    def test_hash_file_matches_stdlib(self, tmp_path):
        """sha256 через hashlib, без утилиты `sha256sum`."""
        import hashlib

        p = tmp_path / "f"
        p.write_bytes(b"payload")
        assert hash_file(p) == hashlib.sha256(b"payload").hexdigest()


class TestSysPathBarrier:
    def test_allowed_roots(self, tmp_path):
        roots = policy.allowed_import_roots(tmp_path)
        assert str(tmp_path / "lib" / "nemeq-core") in roots
        assert str(tmp_path / "lib" / "pydeps") in roots

    def test_hostile_entries_detected(self, tmp_path, monkeypatch):
        """M-2.4-a: site-packages в sys.path обязан обнаруживаться."""
        monkeypatch.setattr(sys, "path", [
            str(tmp_path / "lib" / "nemeq-core"),
            "/usr/lib/python3/dist-packages",
            "/home/u/.local/lib/python3.12/site-packages",
        ])
        bad = policy.hostile_path_entries(tmp_path)
        assert any("dist-packages" in b for b in bad)
        assert any(".local/lib" in b for b in bad)
        assert not any("nemeq-core" in b for b in bad)

    def test_clean_path_has_no_hostile(self, tmp_path):
        original = list(sys.path)
        try:
            sys.path[:] = [str(tmp_path / "lib" / "nemeq-core"),
                           str(tmp_path / "lib" / "pydeps")]
            assert policy.hostile_path_entries(tmp_path) == []
        finally:
            sys.path[:] = original

    def test_sanitize_removes_hostile(self, tmp_path, monkeypatch):
        hostile = "/usr/lib/python3/dist-packages"
        monkeypatch.setattr(sys, "path", [
            str(tmp_path / "lib" / "nemeq-core"),
            str(tmp_path / "lib" / "pydeps"),
            hostile,
        ])
        removed = policy.sanitize_path(tmp_path)
        assert any("dist-packages" in r for r in removed)
        assert not policy.hostile_path_entries(tmp_path)
