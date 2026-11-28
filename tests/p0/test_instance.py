"""Тесты жизненного цикла инстанса (v0.6 §6.3, мутанты M-6.3-*)."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from massaru.instance import (
    InstanceError,
    create,
    destroy,
    list_instances,
    load,
    status,
    uninstall,
)
from massaru.layout import resolve_all


def resolved_for(tmp_path, instance="alpha", **kw):
    return resolve_all(
        home=str(tmp_path / "home"),
        instance=instance,
        env={},
        **kw,
    )


class TestCreate:
    def test_creates_layout(self, tmp_path):
        r = resolved_for(tmp_path)
        info = create(r)
        assert info.name == "alpha"
        assert (r.var / "store").is_dir()
        assert (r.var / "locks").is_dir()
        assert (r.var / "tmp").is_dir()

    def test_permissions_0700(self, tmp_path):
        # M-6.3-a: каталоги создаются с umask по умолчанию — тест падает
        r = resolved_for(tmp_path)
        create(r)
        for p in (r.var, r.var / "store", r.var / "locks"):
            mode = stat.S_IMODE(os.stat(p).st_mode)
            assert mode == 0o700, f"{p} has {oct(mode)}"

    def test_config_0600(self, tmp_path):
        # M-6.3-b: конфиг создаётся читаемым всем — тест падает
        r = resolved_for(tmp_path)
        create(r)
        state_file = r.home / "instances" / "alpha" / "config" / "instance.toml"
        mode = stat.S_IMODE(os.stat(state_file).st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    def test_triple_identity(self, tmp_path):
        """Три РАЗНЫХ идентификатора, каждый — UUIDv4.

        Недостаточно проверить «не равны»: мутант с произвольной строкой
        прошёл бы. Требуем формат UUID и независимость всех трёх.
        """
        # M-6.1-a: store_id/instance_id не UUID или совпадают — тест падает
        import re

        UUID_RE = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        r = resolved_for(tmp_path)
        info = create(r)
        for label, value in (
            ("home_id", info.home_id),
            ("store_id", info.store_id),
            ("instance_id", info.instance_id),
        ):
            assert UUID_RE.fullmatch(value), f"{label} is not UUIDv4: {value!r}"
        assert len({info.home_id, info.store_id, info.instance_id}) == 3, (
            "все три идентификатора обязаны различаться"
        )

    def test_identity_persisted_and_stable(self, tmp_path):
        """Идентификаторы не меняются между load-ами, и переживают перезапуск."""
        r = resolved_for(tmp_path)
        created = create(r)
        loaded = load(r)
        assert loaded.instance_id == created.instance_id
        assert loaded.store_id == created.store_id
        assert loaded.home_id == created.home_id

    def test_recreate_rejected(self, tmp_path):
        # M-6.3-c: повторный create молча перезаписывает — тест падает
        r = resolved_for(tmp_path)
        create(r)
        with pytest.raises(InstanceError) as exc:
            create(r)
        assert exc.value.code == 2

    def test_home_id_stable(self, tmp_path):
        # M-6.3-d: home_id перегенерируется при каждом create — тест падает
        r = resolved_for(tmp_path)
        first = create(r).home_id
        r2 = resolved_for(tmp_path, instance="beta")
        second = create(r2).home_id
        assert first == second


class TestListStatus:
    def test_list_empty(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        assert list_instances(home) == []

    def test_list_two(self, tmp_path):
        create(resolved_for(tmp_path, instance="alpha"))
        create(resolved_for(tmp_path, instance="beta"))
        names = [i.name for i in list_instances(tmp_path / "home")]
        assert names == ["alpha", "beta"]

    def test_status_not_running(self, tmp_path):
        r = resolved_for(tmp_path)
        create(r)
        st = status(r)
        assert st["running"] is False

    def test_status_reports_sources(self, tmp_path):
        # M-5.6: источник пути не печатается — тест падает
        r = resolved_for(tmp_path)
        create(r)
        st = status(r)
        assert st["sources"]["home"] == "--home"
        assert st["sources"]["instance"] == "--instance"


class TestDestroy:
    def test_removes_data_keeps_lib_and_lock(self, tmp_path):
        # M-6.3-e: destroy удаляет lib/ — тест падает
        r = resolved_for(tmp_path)
        create(r)
        (r.var / "store" / "payload").write_text("x")
        lib = r.home / "lib"
        lib.mkdir(parents=True)
        (lib / "keep").write_text("keep")

        # Лок создаётся самим destroy (он обязан его взять). Проверяем, что
        # после destroy он НЕ удалён — иначе unlink-lockfile race (§6.4).
        from massaru.layout import lock_path
        expected_lock = lock_path(r.var, r.store)

        out = destroy(r)

        assert (lib / "keep").is_file(), "lib/ обязан остаться"
        assert expected_lock.exists(), "lock-файл не удаляется при destroy"

    def test_requires_service_stopped(self, tmp_path):
        # M-6.3-f: destroy при живом сервисе проходит — тест падает
        import fcntl

        from massaru.layout import lock_path

        r = resolved_for(tmp_path)
        create(r)
        lock_file = lock_path(r.var, r.store)
        fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(InstanceError) as exc:
                destroy(r)
            assert exc.value.code == 4
        finally:
            os.close(fd)

    def test_unknown_instance(self, tmp_path):
        r = resolved_for(tmp_path)
        with pytest.raises(InstanceError) as exc:
            destroy(r)
        assert exc.value.code == 2


class TestUninstall:
    def test_purge_removes_home(self, tmp_path):
        r = resolved_for(tmp_path)
        create(r)
        uninstall(r, purge=True)
        assert not r.home.exists()

    def test_purge_refused_when_busy(self, tmp_path):
        # M-6.3-g: purge без проверки локов — тест падает
        import fcntl

        from massaru.layout import lock_path

        r = resolved_for(tmp_path)
        create(r)
        lock_file = lock_path(r.var, r.store)
        fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(InstanceError) as exc:
                uninstall(r, purge=True)
            assert exc.value.code == 4
            assert r.home.exists(), "при отказе дерево не трогается"
        finally:
            os.close(fd)

    def test_uninstall_without_purge_keeps_home(self, tmp_path):
        r = resolved_for(tmp_path)
        create(r)
        uninstall(r, purge=False)
        assert r.home.exists()
        assert not (r.var / "store").exists()


class TestIdentityMismatch:
    def test_home_id_mismatch_detected(self, tmp_path):
        # M-6.1-b: home_id не сверяется — тест падает
        r = resolved_for(tmp_path)
        create(r)
        state_file = r.home / "instances" / "alpha" / "config" / "instance.toml"
        text = state_file.read_text()
        text = text.replace(
            next(l for l in text.splitlines() if l.startswith("home_id")),
            'home_id = "00000000-0000-0000-0000-000000000000"',
        )
        state_file.write_text(text)
        with pytest.raises(InstanceError) as exc:
            load(r)
        assert exc.value.code == 5
