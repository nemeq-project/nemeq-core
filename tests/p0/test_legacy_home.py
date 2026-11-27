"""Тесты перехода уже развёрнутых alpha/beta (v0.6 §7.10).

Мутанты M-7.10-a/b/c плюс проверки порядка: манифесты → lib/ → схема →
версия → коллизия → копирование.
"""
from __future__ import annotations

import os
import tomllib
import uuid
from pathlib import Path

import pytest

from massaru.legacy_home import (
    LegacyPlan,
    adopt,
    check_lib_source,
    find_store_collision,
    merge_homes_refused,
    read_manifest,
)
from massaru.migrate import ExitCode, MigrateError


def make_source_home(root: Path, *, store_id: str = "store-a1",
                     schema: int = 4, version: str = "0.1.0",
                     name: str = "alpha", content: bytes = b'{"rev":1}\n') -> Path:
    """Источник в раскладке v0.3: HOME и есть инстанс."""
    home = root / "old_home"
    (home / "var" / "store").mkdir(parents=True)
    (home / "var" / "store" / "seg.jsonl").write_bytes(content)
    (home / "home.toml").write_text(f'layout_version = 3\nhome_id = "h-{store_id}"\n')
    (home / "instance.toml").write_text(
        f'name = "{name}"\n'
        f'store_id = "{store_id}"\n'
        f'schema_version = {schema}\n'
        f'nemeq_version = "{version}"\n'
    )
    return home


def make_lib(root: Path, *, schema: int = 4, version: str = "0.1.0") -> Path:
    lib = root / "lib_src"
    lib.mkdir()
    (lib / "schema_version").write_text(f"{schema}\n")
    (lib / "VERSION").write_text(f"{version}\n")
    return lib


def plan_for(root: Path, home: Path, lib: Path, name: str = "alpha", **kw) -> LegacyPlan:
    target_home = kw.get("target_home", root / "new_home")
    return LegacyPlan(
        from_home=home,
        into_instance=name,
        use_lib_from=lib,
        target_home=target_home,
        run_dir=kw.get("run_dir", root / "run"),
    )


class TestManifest:
    def test_reads_all_fields(self, tmp_path):
        home = make_source_home(tmp_path)
        m = read_manifest(home)
        assert m.store_id == "store-a1"
        assert m.schema_version == 4
        assert m.nemeq_version == "0.1.0"
        assert m.instance_name == "alpha"
        assert m.var == home / "var"

    def test_missing_home_toml(self, tmp_path):
        home = tmp_path / "broken"
        (home / "var").mkdir(parents=True)
        with pytest.raises(MigrateError) as exc:
            read_manifest(home)
        assert exc.value.code == ExitCode.BAD_INVOCATION

    def test_missing_instance_toml(self, tmp_path):
        home = tmp_path / "broken2"
        (home / "var").mkdir(parents=True)
        (home / "home.toml").write_text('layout_version = 3\n')
        with pytest.raises(MigrateError) as exc:
            read_manifest(home)
        assert exc.value.code == ExitCode.BAD_INVOCATION


class TestLibSource:
    def test_missing_lib_refused(self, tmp_path):
        home = make_source_home(tmp_path)
        plan = plan_for(tmp_path, home, tmp_path / "nope")
        with pytest.raises(MigrateError) as exc:
            check_lib_source(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION

    def test_lib_equal_source_refused(self, tmp_path):
        """M-7.10-a: --use-lib-from == --from-home → код 2.

        Иначе версия берётся из мигрируемого HOME — ровно противоречие v0.3.
        """
        home = make_source_home(tmp_path)
        make_lib(tmp_path)
        plan = plan_for(tmp_path, home, home)
        with pytest.raises(MigrateError) as exc:
            check_lib_source(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION
        assert "must not point at the source" in exc.value.message

    def test_lib_symlink_to_source_refused(self, tmp_path):
        """Симлинк на источник — тот же обход, должен отвергаться."""
        home = make_source_home(tmp_path)
        link = tmp_path / "lib_link"
        link.symlink_to(home, target_is_directory=True)
        plan = plan_for(tmp_path, home, link)
        with pytest.raises(MigrateError) as exc:
            check_lib_source(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION

    def test_lib_equal_target_refused(self, tmp_path):
        home = make_source_home(tmp_path)
        target = tmp_path / "new_home"
        target.mkdir()
        (target / "schema_version").write_text("4\n")
        plan = plan_for(tmp_path, home, target)
        with pytest.raises(MigrateError) as exc:
            check_lib_source(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION

    def test_valid_lib_accepted(self, tmp_path):
        home = make_source_home(tmp_path)
        lib = make_lib(tmp_path)
        check_lib_source(plan_for(tmp_path, home, lib))


class TestVersionChecks:
    def test_schema_too_new(self, tmp_path):
        """§7.10 п.3: схема источника выше поддерживаемой lib → код 5."""
        home = make_source_home(tmp_path, schema=9)
        lib = make_lib(tmp_path, schema=4)
        with pytest.raises(MigrateError) as exc:
            adopt(plan_for(tmp_path, home, lib))
        assert exc.value.code == ExitCode.IDENTITY_MISMATCH

    def test_version_skew(self, tmp_path):
        """§7.10 п.4: расхождение nemeq_version → код 13, флага обхода нет."""
        home = make_source_home(tmp_path, version="0.0.9")
        lib = make_lib(tmp_path, version="0.1.0")
        with pytest.raises(MigrateError) as exc:
            adopt(plan_for(tmp_path, home, lib))
        assert exc.value.code == ExitCode.VERSION_SKEW

    def test_matching_versions_pass(self, tmp_path):
        home = make_source_home(tmp_path)
        lib = make_lib(tmp_path)
        rep = adopt(plan_for(tmp_path, home, lib))
        assert rep.state == "adopted"


class TestCollision:
    def test_same_store_id_refused(self, tmp_path):
        """M-7.10-b: две копии одного стора → вторая даёт код 5.

        Сообщение обязано назвать первый инстанс и показать, копия это или
        дивергенция — иначе непонятно, что делать с данными.
        """
        home1 = make_source_home(tmp_path / "s1", store_id="same-store")
        home2 = make_source_home(tmp_path / "s2", store_id="same-store",
                                 content=b'{"rev":1}\n')
        lib = make_lib(tmp_path, version="0.1.0")

        first = adopt(plan_for(tmp_path / "s1", home1, lib, name="alpha"))
        assert first.store_id == "same-store"

        with pytest.raises(MigrateError) as exc:
            adopt(plan_for(tmp_path / "s2", home2, lib, name="beta",
                           target_home=tmp_path / "s1" / "new_home"))
        assert exc.value.code == ExitCode.IDENTITY_MISMATCH
        assert "alpha" in exc.value.message
        assert "identical copy" in exc.value.message

    def test_diverged_copy_reported(self, tmp_path):
        """Коллизия с разным содержимым сообщает «diverged», а не «копия»."""
        home1 = make_source_home(tmp_path / "s1", store_id="dup", content=b'{"a":1}\n')
        home2 = make_source_home(tmp_path / "s2", store_id="dup", content=b'{"a":2}\n')
        lib = make_lib(tmp_path)

        adopt(plan_for(tmp_path / "s1", home1, lib, name="alpha"))
        with pytest.raises(MigrateError) as exc:
            adopt(plan_for(tmp_path / "s2", home2, lib, name="beta",
                           target_home=tmp_path / "s1" / "new_home"))
        assert exc.value.code == ExitCode.IDENTITY_MISMATCH
        # Нельзя искать просто "diverged": tmp_path теста содержит имя
        # test_diverged_copy_reported0, и эта подстрока попадает в сообщение
        # через путь. Проверяем маркер целиком, вместе с пояснением.
        assert "diverged (digest differs)" in exc.value.message, exc.value.message

    def test_different_store_ids_both_pass(self, tmp_path):
        """M-7.10-c: разные store_id → обе проходят, instance_id уникальны."""
        lib = make_lib(tmp_path)
        base = tmp_path / "multi"

        h1 = make_source_home(base / "s1", store_id="store-one", name="alpha")
        h2 = make_source_home(base / "s2", store_id="store-two", name="beta")

        r1 = adopt(plan_for(base, h1, lib, name="alpha"))
        r2 = adopt(plan_for(base, h2, lib, name="beta"))

        assert r1.store_id == "store-one"
        assert r2.store_id == "store-two"
        assert r1.instance_id != r2.instance_id, "instance_id не уникален"
        uuid.UUID(r1.instance_id), uuid.UUID(r2.instance_id)

    def test_collision_lookup_finds_instance(self, tmp_path):
        home = make_source_home(tmp_path / "s1", store_id="seek-me")
        lib = make_lib(tmp_path)
        adopt(plan_for(tmp_path / "s1", home, lib, name="alpha"))
        found = find_store_collision(tmp_path / "s1" / "new_home", "seek-me")
        assert found is not None
        assert found.name == "alpha"


class TestCopyScope:
    def test_only_var_copied(self, tmp_path):
        """§7.10 п.5: копируется только var/. Манифесты источника не тащатся."""
        home = make_source_home(tmp_path)
        (home / "logs").mkdir()
        (home / "logs" / "old.log").write_text("x")
        lib = make_lib(tmp_path)
        rep = adopt(plan_for(tmp_path, home, lib))

        target = tmp_path / "new_home" / "instances" / "alpha"
        assert (target / "var" / "store" / "seg.jsonl").is_file()
        assert not (target / "logs").exists(), "logs/ не должны переноситься"
        assert rep.files >= 1

    def test_instance_id_is_new(self, tmp_path):
        """§7.10 п.6: instance_id новый, store_id сохранён, lineage заполнен."""
        home = make_source_home(tmp_path, store_id="keep-me")
        lib = make_lib(tmp_path)
        rep = adopt(plan_for(tmp_path, home, lib))

        assert rep.store_id == "keep-me"
        # Непустоты мало: layout-vN остаётся и без записи происхождения.
        # Проверяем, что lineage называет инстанс-источник и store_id.
        assert any("@store:keep-me" in item for item in rep.lineage), \
            f"lineage не фиксирует происхождение: {rep.lineage}"

        cfg = tmp_path / "new_home" / "instances" / "alpha" / "config" / "instance.toml"
        data = tomllib.loads(cfg.read_text())
        assert data["store_id"] == "keep-me"
        assert data["instance_id"] == rep.instance_id
        assert any("@store:keep-me" in item for item in data["lineage"])


class TestMergeRefused:
    def test_two_homes_refused(self, tmp_path):
        """§7.10 п.8: слияние двух HOME в один инстанс → код 2."""
        with pytest.raises(MigrateError) as exc:
            merge_homes_refused([tmp_path / "a", tmp_path / "b"])
        assert exc.value.code == ExitCode.BAD_INVOCATION
        assert "separately" in exc.value.message

    def test_single_home_allowed(self, tmp_path):
        merge_homes_refused([tmp_path / "a"])


class TestNoPartialTarget:
    def test_failed_version_check_leaves_no_target(self, tmp_path):
        """Отказ до копирования не оставляет полу-инстанса."""
        home = make_source_home(tmp_path, version="9.9.9")
        lib = make_lib(tmp_path, version="0.1.0")
        with pytest.raises(MigrateError):
            adopt(plan_for(tmp_path, home, lib))
        assert not (tmp_path / "new_home" / "instances" / "alpha").exists()

    def test_existing_target_refused(self, tmp_path):
        home = make_source_home(tmp_path)
        lib = make_lib(tmp_path)
        target = tmp_path / "new_home" / "instances" / "alpha"
        target.mkdir(parents=True)
        with pytest.raises(MigrateError) as exc:
            adopt(plan_for(tmp_path, home, lib))
        assert exc.value.code == ExitCode.BAD_INVOCATION
