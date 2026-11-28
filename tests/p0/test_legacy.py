"""Тесты legacy-таблицы (v0.6 §4.1, §4.2, §4.8; мутанты M-4.1-*, M-4.2-a, M-4.8-b/c/d)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from massaru import legacy
from massaru.layout import PathError, resolve_all, resolve_home, resolve_instance


class TestMappingTable:
    def test_all_roots_known(self):
        roots = legacy.legacy_roots()
        assert "/var/nemeq" in roots
        assert "/var/lib/nemeq" in roots
        assert "/opt/nemeq" in roots
        assert "/run/nemeq" in roots

    def test_apiserver_maps_to_store(self):
        """§4.1: apiserver -> var/store — это ПЕРЕИМЕНОВАНИЕ, не копия папки."""
        assert legacy.map_entry("/var/nemeq/apiserver") == "store"

    def test_var_lib_maps_to_store(self):
        assert legacy.map_entry("/var/lib/nemeq") == "store"

    def test_epishu_keeps_level(self):
        """§4.1: уровень вложенности внутри epishu/<id>/ не меняется."""
        assert legacy.map_entry("/var/nemeq/epishu") == "epishu"
        got = legacy.map_entry("/var/nemeq/epishu/e1/wal/0001.jsonl")
        assert got == "epishu/e1/wal/0001.jsonl"

    def test_unknown_path_raises_7(self):
        """M-4.1-a: нет правила -> код 7 с именем файла."""
        with pytest.raises(legacy.LegacyError) as exc:
            legacy.map_entry("/var/nemeq/junk.bin")
        assert exc.value.code == 7
        assert "junk.bin" in exc.value.message

    def test_opt_and_run(self):
        assert legacy.map_entry("/opt/nemeq") == "lib"
        assert legacy.map_entry("/run/nemeq") == "run"

    def test_is_legacy_path(self):
        assert legacy.is_legacy_path("/var/nemeq/state")
        assert legacy.is_legacy_path("/var/lib/nemeq")
        assert not legacy.is_legacy_path("/home/user/.nemeq")


class TestSourceScan:
    def _make(self, root: Path, names: list[str]) -> Path:
        var = root / "var"
        var.mkdir(parents=True)
        for n in names:
            (var / n).mkdir()
        return var

    def test_known_entries_accepted(self, tmp_path):
        var = self._make(tmp_path, ["store", "epishu", "state", "data"])
        result = legacy.scan_source(var)
        targets = {t for _, t, _ in result["plan"]}
        assert targets == {"store", "epishu", "state", "data"}

    def test_unknown_entry_rejected(self, tmp_path):
        """M-4.1-a: файл вне таблицы -> код 7 и имя."""
        var = self._make(tmp_path, ["store", "junk"])
        with pytest.raises(legacy.LegacyError) as exc:
            legacy.scan_source(var)
        assert exc.value.code == 7
        assert "junk" in exc.value.message

    def test_internal_dirs_skipped(self, tmp_path):
        var = self._make(tmp_path, ["store", "tmp", "locks"])
        result = legacy.scan_source(var)
        names = {n for n, _, _ in result["plan"]}
        assert "tmp" not in names and "locks" not in names

    def test_conflict_two_store_sources(self, tmp_path):
        """M-4.1-b: apiserver и lib одновременно -> конфликт цели -> код 7."""
        var = self._make(tmp_path, ["apiserver", "lib"])
        with pytest.raises(legacy.LegacyError) as exc:
            legacy.scan_source(var)
        assert exc.value.code == 7
        assert "conflict" in exc.value.message.lower()

    def test_detect_conflict_helper(self, tmp_path):
        var = self._make(tmp_path, ["apiserver", "lib"])
        assert legacy.detect_store_conflict(var) is not None

    def test_no_conflict_single_store(self, tmp_path):
        var = self._make(tmp_path, ["apiserver"])
        assert legacy.detect_store_conflict(var) is None

    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(legacy.LegacyError):
            legacy.scan_source(tmp_path / "nope")


class TestAbsolutePathsInData:
    def test_absolute_detected(self):
        """M-4.2-a: абсолютный путь внутри данных должен обнаруживаться."""
        assert legacy.contains_absolute_path("/var/nemeq/state/x")
        assert legacy.contains_absolute_path("/etc/passwd")

    def test_relative_ok(self):
        assert not legacy.contains_absolute_path("epishu/e1/wal/0001.jsonl")
        assert not legacy.contains_absolute_path("")
        assert not legacy.contains_absolute_path("store/log")


class TestResolverErrors:
    def test_relative_home_rejected(self):
        """M-4.8-b: относительный NEMEQ_HOME -> код 2."""
        with pytest.raises(PathError):
            resolve_home("./x", env={})

    def test_relative_env_home_rejected(self):
        with pytest.raises(PathError):
            resolve_home(None, env={"NEMEQ_HOME": "relative/path"})

    def test_parent_instance_rejected(self):
        """M-4.8-c: --instance ../x -> код 2 до файловых операций."""
        with pytest.raises(PathError):
            resolve_instance("../x", env={})

    def test_slash_instance_rejected(self):
        with pytest.raises(PathError):
            resolve_instance("a/b", env={})

    def test_missing_home_env_rejected(self):
        """M-4.8-d: env без HOME -> код 2, без фоллбэка."""
        with pytest.raises(PathError):
            resolve_home(None, env={})

    def test_empty_home_env_rejected(self):
        with pytest.raises(PathError):
            resolve_home(None, env={"HOME": ""})

    def test_no_traceback_leak_on_relative(self):
        """Проверка, что отказ — PathError с кодом 2, а не голый ValueError."""
        try:
            resolve_home("rel", env={})
        except PathError as exc:
            assert exc.exit_code == 2
        else:
            pytest.fail("ожидался PathError")
