"""Тесты §7.8 (грязный WAL) и §7.9 (приоритет кодов), мутанты M-7.8-*, M-7.9-a."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from massaru.migrate import ExitCode, MigrateError, MigratePlan, migrate
from massaru.migrate_wal import (
    CODE_PRIORITY,
    preflight_wal,
    replay_wal_into,
    resolve_priority,
    wal_tail_state,
)


def make_source(root: Path, *, dirty: bool = False) -> Path:
    var = root / "source_var"
    log = var / "store" / "log"
    log.mkdir(parents=True)
    line = b'{"rev":1}\n'
    (log / "000001.jsonl").write_bytes(line if not dirty else line + b'{"rev":2,"par')
    (var / "epishu" / "e1" / "wal").mkdir(parents=True)
    (var / "epishu" / "e1" / "wal" / "0001.jsonl").write_text('{"a":1}\n')
    return var


def plan_for(tmp_path: Path, **kw) -> MigratePlan:
    return MigratePlan(
        source_var=kw.get("source", tmp_path / "source_var"),
        target_var=kw.get("target", tmp_path / "inst" / "var"),
        run_dir=kw.get("run_dir", tmp_path / "run"),
        target_uid=kw.get("target_uid", os.geteuid()),
        target_gid=kw.get("target_gid", os.getegid()),
        root_assisted=kw.get("root_assisted", os.geteuid() == 0),
        adopt_uids=kw.get("adopt_uids", []),
    )


class TestWalDetection:
    def test_clean_tail(self, tmp_path):
        var = make_source(tmp_path)
        state = wal_tail_state(var)
        assert state["dirty"] is False

    def test_dirty_tail_detected(self, tmp_path):
        """M-7.8-a: оборванная транзакция обязана обнаруживаться."""
        var = make_source(tmp_path, dirty=True)
        state = wal_tail_state(var)
        assert state["dirty"] is True
        assert state["files"]

    def test_no_log_dir_is_clean(self, tmp_path):
        var = tmp_path / "empty_var"
        (var / "store").mkdir(parents=True)
        assert wal_tail_state(var)["dirty"] is False


class TestDirtyWalRefusal:
    def test_preflight_refuses_without_recover(self, tmp_path):
        """M-7.8-a: без --recover → код 6 и перечень файлов."""
        make_source(tmp_path, dirty=True)
        plan = plan_for(tmp_path)
        with pytest.raises(MigrateError) as exc:
            preflight_wal(plan, recover=False)
        assert exc.value.code == ExitCode.DIRTY_WAL
        assert "000001.jsonl" in exc.value.message

    def test_preflight_allows_with_recover(self, tmp_path):
        make_source(tmp_path, dirty=True)
        plan = plan_for(tmp_path)
        state = preflight_wal(plan, recover=True)
        assert state["dirty"] is True

    def test_clean_source_passes(self, tmp_path):
        make_source(tmp_path)
        plan = plan_for(tmp_path)
        assert preflight_wal(plan)["dirty"] is False


class TestWalReplay:
    def test_replay_trims_tail(self, tmp_path):
        """M-7.8-b: реплей обрезает хвост до последней целой строки."""
        var = make_source(tmp_path, dirty=True)
        staging = tmp_path / "staging"
        (staging / "store" / "log").mkdir(parents=True)
        (staging / "store" / "log" / "000001.jsonl").write_bytes(
            (var / "store" / "log" / "000001.jsonl").read_bytes()
        )
        dropped = replay_wal_into(staging, var)
        assert dropped > 0
        data = (staging / "store" / "log" / "000001.jsonl").read_bytes()
        assert data.endswith(b"\n"), "хвост не обрезан"
        assert b'"par' not in data

    def test_source_untouched(self, tmp_path):
        """§7.8: источник не изменяется — работаем только с копией."""
        var = make_source(tmp_path, dirty=True)
        before = (var / "store" / "log" / "000001.jsonl").read_bytes()
        staging = tmp_path / "staging"
        (staging / "store" / "log").mkdir(parents=True)
        (staging / "store" / "log" / "000001.jsonl").write_bytes(before)
        replay_wal_into(staging, var)
        assert (var / "store" / "log" / "000001.jsonl").read_bytes() == before

    def test_clean_tail_nothing_dropped(self, tmp_path):
        var = make_source(tmp_path)
        staging = tmp_path / "staging"
        (staging / "store" / "log").mkdir(parents=True)
        (staging / "store" / "log" / "000001.jsonl").write_bytes(
            (var / "store" / "log" / "000001.jsonl").read_bytes()
        )
        assert replay_wal_into(staging, var) == 0


class TestCodePriority:
    def test_priority_order_complete(self):
        """M-7.9-a: все коды из таблицы присутствуют в приоритете."""
        assert len(CODE_PRIORITY) == 12
        assert CODE_PRIORITY[0] == ExitCode.BAD_INVOCATION
        assert ExitCode.DATA_DIGEST in CODE_PRIORITY
        assert ExitCode.HEALTH_FAILED in CODE_PRIORITY

    def test_invocation_beats_everything(self):
        """§7.9: код 2 важнее остальных — сначала «можно ли начинать»."""
        got = resolve_priority([ExitCode.DATA_DIGEST, ExitCode.BAD_INVOCATION,
                                ExitCode.LOCK_BUSY])
        assert got == ExitCode.BAD_INVOCATION

    def test_lock_before_fs(self):
        got = resolve_priority([ExitCode.UNSUPPORTED_FS, ExitCode.LOCK_BUSY])
        assert got == ExitCode.LOCK_BUSY

    def test_fs_before_rule(self):
        got = resolve_priority([ExitCode.NO_RULE, ExitCode.UNSUPPORTED_FS])
        assert got == ExitCode.UNSUPPORTED_FS

    def test_dirty_wal_before_identity(self):
        got = resolve_priority([ExitCode.IDENTITY_MISMATCH, ExitCode.DIRTY_WAL])
        assert got == ExitCode.DIRTY_WAL

    def test_digest_last_before_health(self):
        got = resolve_priority([ExitCode.HEALTH_FAILED, ExitCode.DATA_DIGEST])
        assert got == ExitCode.DATA_DIGEST

    def test_empty_returns_none(self):
        assert resolve_priority([]) is None


class TestCodesAreDistinct:
    def test_all_codes_unique(self):
        """Каждый код таблицы §7.9 — отдельное число, без склеек."""
        values = [c.value for c in ExitCode]
        assert len(values) == len(set(values))
        assert set(values) == {0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}

    def test_no_partial_target_on_wal_refusal(self, tmp_path):
        """Отказ по WAL не создаёт частичный target."""
        make_source(tmp_path, dirty=True)
        plan = plan_for(tmp_path)
        with pytest.raises(MigrateError):
            preflight_wal(plan)
        assert not plan.target_var.exists()
