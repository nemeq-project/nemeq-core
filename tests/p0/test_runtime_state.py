"""Тесты runtime-состояния и портов (v0.6 §6.1, §6.2; мутанты M-6.1-c/d/e, M-6.2-a)."""
from __future__ import annotations

import fcntl
import json
import os
import socket
from pathlib import Path

import pytest

from massaru import runtime_state as rs
from massaru.layout import lock_path, resolve_all


def resolved_for(tmp_path, instance="alpha"):
    return resolve_all(home=str(tmp_path / "home"), instance=instance, env={})


def make_instance(resolved):
    """Создать минимальную раскладку без вызова install (preflight не нужен)."""
    from massaru.instance import create
    create(resolved)
    return resolved


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestPortReservation:
    def test_default_port_constant(self):
        assert rs.DEFAULT_PORT == 8448

    def test_free_port_accepted(self, tmp_path):
        r = make_instance(resolved_for(tmp_path))
        port = free_port()
        assert rs.reserve_port(r, port) == port

    def test_busy_port_rejected_with_code_3(self, tmp_path):
        """M-6.2-a: занятый порт → код 3 и имя виновника."""
        r = make_instance(resolved_for(tmp_path))
        port = free_port()

        # Занимаем порт и регистрируем «виновника» в его runtime-файле
        holder = make_instance(resolved_for(tmp_path, instance="beta"))
        rs.write_runtime(holder, rs.RuntimeState(
            name="beta", home_id="h", store_id="s", instance_id="i",
            pid=os.getpid(), port=port, started_at="now",
            store_realpath=str(holder.store),
        ))

        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            with pytest.raises(rs.RuntimeError_) as exc:
                rs.reserve_port(r, port)
            assert exc.value.code == rs.EXIT_PORT_BUSY
            assert "beta" in exc.value.message, exc.value.message
        finally:
            srv.close()

    def test_no_auto_increment(self, tmp_path):
        """§6.2: порт не меняется молча — второго порта не выдаётся."""
        r = make_instance(resolved_for(tmp_path))
        port = free_port()
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            with pytest.raises(rs.RuntimeError_):
                rs.reserve_port(r, port)
            # никакого «взять следующий» не произошло
            assert not rs.port_is_free(port)
        finally:
            srv.close()

    def test_ephemeral_zero_allowed(self, tmp_path):
        r = make_instance(resolved_for(tmp_path))
        assert rs.reserve_port(r, 0) == 0

    def test_owner_lookup_ignores_other_homes(self, tmp_path):
        r = make_instance(resolved_for(tmp_path))
        assert rs.find_port_owner(r.home, 6553) is None


class TestRuntimeFile:
    def test_missing_runtime_is_not_error(self, tmp_path):
        """M-6.1-c: новый инстанс без runtime-файла стартует успешно."""
        r = make_instance(resolved_for(tmp_path))
        assert rs.read_runtime(r) is None
        info = rs.reconcile_identity(r, config_home_id="h", config_store_id="s",
                                     config_instance_id="i")
        assert info["missing"] is True
        assert info["runtimeStale"] is False

    def test_start_creates_runtime_after_lock(self, tmp_path):
        """M-6.1-c: файл создаётся после лока и содержит живые данные."""
        r = make_instance(resolved_for(tmp_path))
        state = rs.start(r, home_id="h", store_id="s", instance_id="i",
                         desired_port=free_port())
        path = rs.runtime_path(r)
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["pid"] == os.getpid()
        assert data["instanceId"] == "i"
        assert data["port"] == state.port

    def test_stale_runtime_overwritten(self, tmp_path):
        """M-6.1-d: протухший файл от убитого процесса перезаписывается."""
        r = make_instance(resolved_for(tmp_path))
        path = rs.runtime_path(r)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": "alpha", "pid": 999999, "instanceId": "old",
            "port": 1234, "homeId": "h", "storeId": "s",
            "startedAt": "old", "storeRealpath": "/x",
        }))
        state = rs.start(r, home_id="h2", store_id="s2", instance_id="new",
                         desired_port=free_port())
        data = json.loads(path.read_text())
        assert data["instanceId"] == "new"
        assert data["homeId"] == "h2"
        assert data["pid"] == os.getpid()
        assert state.instance_id == "new"

    def test_runtime_id_mismatch_does_not_block(self, tmp_path):
        """M-6.1-e: расхождение instance_id в runtime не блокирует старт."""
        r = make_instance(resolved_for(tmp_path))
        path = rs.runtime_path(r)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": "alpha", "pid": 1, "instanceId": "tampered",
            "port": 8448, "homeId": "h", "storeId": "s",
            "startedAt": "x", "storeRealpath": "/x",
        }))
        info = rs.reconcile_identity(r, config_home_id="h", config_store_id="s",
                                     config_instance_id="real")
        assert info["runtimeStale"] is True
        assert info["authoritative"]["instanceId"] == "real"

        # старт проходит и перезаписывает файл из instance.toml
        rs.start(r, home_id="h", store_id="s", instance_id="real",
                 desired_port=free_port())
        assert json.loads(path.read_text())["instanceId"] == "real"

    def test_runtime_permissions_0600(self, tmp_path):
        r = make_instance(resolved_for(tmp_path))
        rs.start(r, home_id="h", store_id="s", instance_id="i",
                 desired_port=free_port())
        mode = os.stat(rs.runtime_path(r)).st_mode & 0o777
        assert mode == 0o600

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        r = make_instance(resolved_for(tmp_path))
        rs.start(r, home_id="h", store_id="s", instance_id="i",
                 desired_port=free_port())
        leftovers = list(rs.runtime_path(r).parent.glob("*.tmp"))
        assert not leftovers, f"остались временные файлы: {leftovers}"


class TestLockFirstProtocol:
    def test_start_fails_when_store_busy(self, tmp_path):
        """M-6.1-c: без лока старт не проходит — иначе runtime пишется вслепую."""
        r = make_instance(resolved_for(tmp_path))
        lock_file = lock_path(r.var, r.store)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(rs.RuntimeError_) as exc:
                rs.start(r, home_id="h", store_id="s", instance_id="i")
            assert exc.value.code == rs.EXIT_LOCK_BUSY
        finally:
            os.close(fd)

    def test_two_starts_one_wins(self, tmp_path):
        """Только один процесс может держать лок стора одновременно."""
        r = make_instance(resolved_for(tmp_path))
        lock_file = lock_path(r.var, r.store)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd1 = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(rs.RuntimeError_):
                rs.start(r, home_id="h", store_id="s", instance_id="i")
        finally:
            os.close(fd1)

        # после освобождения лока старт проходит
        state = rs.start(r, home_id="h", store_id="s", instance_id="i",
                         desired_port=free_port())
        assert state.instance_id == "i"
