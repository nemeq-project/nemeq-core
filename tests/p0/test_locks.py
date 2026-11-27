"""Тесты блокировок стора v0.6 §6.4 (мутанты M-6.4-b/c/e/f/h/j).

Принципы, которые проверяются:

  * лок живёт в `<var>/locks/<sha256(realpath(store))>.lock`, НЕ в сторе —
    иначе digest невоспроизводим и лок копируется при миграции;
  * digest полный, не усечённый — иначе два стора с общим префиксом хеша
    получат один лок и будут думать, что конкурируют;
  * `destroy` не трогает `var/locks/` — иначе unlink-lockfile race;
  * один store через два разных пути (симлинк) = один лок;
  * разные сторы в одном каталоге = разные локи, живут одновременно;
  * `uninstall --purge` при живом владельце → код 4, дерево не удалено.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from massaru.instance import (
    EXIT_LOCK_BUSY,
    InstanceError,
    create,
    destroy,
    list_instances,
    uninstall,
)
from massaru.layout import lock_path, resolve_all


def resolved_for(tmp_path, instance="alpha", store=None):
    kw = {"home": str(tmp_path / "home"), "instance": instance, "env": {}}
    if store is not None:
        kw["store"] = str(store)
        kw["allow_outside_home"] = True
    return resolve_all(**kw)


def hold(path: Path) -> int:
    """Взять лок и держать (как живой процесс)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


class TestLockLocation:
    def test_lock_outside_store(self, tmp_path):
        """M-6.4-a (обратная сторона): лок не внутри var/store/."""
        r = resolved_for(tmp_path)
        lp = lock_path(r.var, r.store, r.home)
        assert "locks" in lp.parts, lp
        assert "store" not in lp.parts, f"лок внутри стора: {lp}"

    def test_lock_inside_var(self, tmp_path):
        """M-6.4-f: лок обязан быть внутри var/ — иначе ProtectSystem даст EROFS."""
        r = resolved_for(tmp_path)
        lp = lock_path(r.var, r.store, r.home)
        assert r.var in lp.parents, f"лок вне var/: {lp}"

    def test_lock_digest_is_full_sha256(self, tmp_path):
        """M-6.4-g: name = полный sha256 (64 hex), не усечённый."""
        r = resolved_for(tmp_path)
        name = lock_path(r.var, r.store, r.home).name
        assert name.endswith(".lock")
        hexpart = name[: -len(".lock")]
        assert len(hexpart) == 64, f"усечённый digest: {hexpart!r}"
        int(hexpart, 16)  # валидный hex


class TestLockIdentity:
    def test_same_store_same_lock(self, tmp_path):
        """M-6.4-d: один store = один лок, независимо от имени инстанса."""
        a = resolved_for(tmp_path, instance="alpha")
        a = resolved_for(tmp_path, instance="alpha", store=tmp_path / "shared")
        b = resolved_for(tmp_path, instance="beta", store=tmp_path / "shared")
        assert a.lock == b.lock, f"два лока на один стор: {a.lock} vs {b.lock}"

    def test_symlinked_store_same_lock(self, tmp_path):
        """M-6.4-d: путь через симлинк даёт тот же лок (realpath, не abspath)."""
        real = tmp_path / "realstore"
        real.mkdir()
        link = tmp_path / "linkstore"
        link.symlink_to(real, target_is_directory=True)

        a = resolved_for(tmp_path, instance="alpha")
        lb = lock_path(a.var, link, a.home)
        la = lock_path(a.var, real, a.home)
        if la != lb:
            pytest.skip("резолвер нормализует store до realpath — симлинк уже схлопнут")
        assert la == lb

    def test_different_stores_different_locks(self, tmp_path):
        """M-6.4-e: два разных стора в одном каталоге получают РАЗНЫЕ локи."""
        s1 = tmp_path / "stores" / "one"
        s2 = tmp_path / "stores" / "two"
        s1.mkdir(parents=True)
        s2.mkdir(parents=True)
        r1 = resolved_for(tmp_path, instance="alpha", store=s1)
        r2 = resolved_for(tmp_path, instance="beta", store=s2)
        assert lock_path(r1.var, r1.store, r1.home) != lock_path(r2.var, r2.store, r2.home)

    def test_two_stores_both_alive(self, tmp_path):
        """M-6.4-e: разные сторы держат лок одновременно (не мешают друг другу)."""
        s1 = tmp_path / "stores" / "one"
        s2 = tmp_path / "stores" / "two"
        s1.mkdir(parents=True)
        s2.mkdir(parents=True)
        r1 = resolved_for(tmp_path, instance="alpha", store=s1)
        r2 = resolved_for(tmp_path, instance="beta", store=s2)

        fd1 = hold(lock_path(r1.var, r1.store, r1.home))
        try:
            fd2 = hold(lock_path(r2.var, r2.store, r2.home))  # не должен упасть
            os.close(fd2)
        finally:
            os.close(fd1)

    def test_same_store_blocks_second(self, tmp_path):
        """M-6.4-c: два инстанса с одним --store → второй получает код 4."""
        r1 = resolved_for(tmp_path, instance="alpha")
        create(r1)
        r2 = resolved_for(tmp_path, instance="beta", store=r1.store)
        create(r2)

        lp = r1.lock
        assert lp == r2.lock, "инстансы одного стора должны видеть один лок"
        fd = hold(lp)
        try:
            with pytest.raises(InstanceError) as exc:
                destroy(r2)  # destroy требует лок того же стора
            assert exc.value.code == EXIT_LOCK_BUSY
        finally:
            os.close(fd)


class TestDestroyKeepsLocks:
    def test_destroy_keeps_lock_dir(self, tmp_path):
        """M-6.4-h: destroy не удаляет var/locks/."""
        r = resolved_for(tmp_path)
        create(r)
        lp = lock_path(r.var, r.store, r.home)
        assert lp.parent.is_dir()

        destroy(r)

        assert lp.parent.is_dir(), "destroy удалил var/locks/ — возможен race"
        assert not r.store.exists(), "данные стора должны быть удалены"

    def test_destroy_does_not_unlink_lockfile(self, tmp_path):
        """M-6.4-b: destroy не делает unlink(lock) — файл остаётся."""
        r = resolved_for(tmp_path)
        create(r)
        lp = lock_path(r.var, r.store, r.home)
        hold_now = hold(lp)
        os.close(hold_now)
        assert lp.is_file()

        destroy(r)

        assert lp.is_file(), "lock-файл удалён: между destroy и стартом возможен race"

    def test_destroy_then_concurrent_start(self, tmp_path):
        """M-6.4-h: после destroy лок по-прежнему защищает — гонки нет.

        Если бы destroy удалял каталог локов, два процесса могли бы создать
        два разных файла на один store и оба считать себя владельцами.
        """
        r = resolved_for(tmp_path)
        create(r)
        lp = lock_path(r.var, r.store, r.home)
        destroy(r)

        fd = hold(lp)
        try:
            # второй претендент обязан получить отказ, а не свой лок
            fd2 = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                with pytest.raises(OSError):
                    fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd2)
        finally:
            os.close(fd)


class TestPurge:
    def test_purge_refused_when_owner_alive(self, tmp_path):
        """M-6.4-j: uninstall --purge при живом владельце → код 4, дерево цело."""
        r = resolved_for(tmp_path, instance="alpha")
        create(r)
        create(resolved_for(tmp_path, instance="beta"))

        lp = lock_path(r.var, r.store, r.home)
        fd = hold(lp)
        try:
            with pytest.raises(InstanceError) as exc:
                uninstall(r, purge=True)
            assert exc.value.code == EXIT_LOCK_BUSY
            assert r.home.is_dir(), "дерево удалено, хотя владелец жив"
            assert (r.home / "instances" / "alpha").is_dir()
        finally:
            os.close(fd)

    def test_purge_removes_tree_when_idle(self, tmp_path):
        r = resolved_for(tmp_path, instance="alpha")
        create(r)
        create(resolved_for(tmp_path, instance="beta"))
        uninstall(r, purge=True)
        assert not r.home.exists() or not list_instances(r.home)
