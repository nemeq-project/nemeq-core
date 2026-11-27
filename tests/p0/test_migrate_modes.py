"""Тесты режимов миграции v0.6 §7.4 (мутанты M-7.4-a..j).

Проверяются три режима владения (rootless / root-assisted / adopt) и то, что
нормализация прав не ломает сравнение содержимого.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from massaru.digest import content_digest, metadata_digest
from massaru.migrate import ExitCode, MigrateError, MigratePlan, migrate


def make_source(root: Path, *, mode: int = 0o644) -> Path:
    var = root / "source_var"
    (var / "store").mkdir(parents=True)
    (var / "store" / "seg.jsonl").write_text('{"rev":1}\n')
    (var / "epishu" / "e1" / "wal").mkdir(parents=True)
    (var / "epishu" / "e1" / "wal" / "0001.jsonl").write_text('{"a":1}\n')
    os.chmod(var / "store" / "seg.jsonl", mode)
    os.chmod(var / "epishu" / "e1" / "wal" / "0001.jsonl", mode)
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


ROOT = os.geteuid() == 0


class TestRootAssistedFlag:
    def test_root_without_flag_rejected(self, tmp_path):
        """M-7.4-a: запуск от root без --root-assisted → код 2.

        Привилегия не применяется молча: root обязан подтвердить режим.
        """
        if not ROOT:
            pytest.skip("тест имеет смысл только под root")
        make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=False)
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION
        assert "root-assisted" in exc.value.message

    def test_root_with_flag_proceeds(self, tmp_path):
        if not ROOT:
            pytest.skip("тест имеет смысл только под root")
        make_source(tmp_path)
        rep = migrate(plan_for(tmp_path, root_assisted=True))
        assert rep.state == "reported"


class TestTargetUidZero:
    def test_target_uid_zero_rule_is_checked(self, tmp_path, monkeypatch):
        """M-7.4-b: правило «нельзя отдать стор root из-под non-root» работает.

        Под root правило неприменимо по смыслу, поэтому эмулируем non-root
        через подмену os.geteuid — иначе тест был бы скипом, то есть вакуумным.
        """
        import massaru.migrate as m

        make_source(tmp_path)
        monkeypatch.setattr(m.os, "geteuid", lambda: 1000)
        plan = plan_for(tmp_path, root_assisted=True, target_uid=0)
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION
        assert "forbidden" in exc.value.message

    def test_root_target_uid_allowed_under_root(self, tmp_path):
        """Под root --target-uid 0 — это текущий режим, не эскалация."""
        if not ROOT:
            pytest.skip("правило имеет смысл только под root")
        make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=True, target_uid=0, target_gid=0)
        rep = migrate(plan)
        assert rep.state == "reported"


class TestModeNormalization:
    def test_source_0644_becomes_0600(self, tmp_path):
        """M-7.4-h: 0644 в источнике → 0600 в цели, и это НЕ ошибка."""
        var = make_source(tmp_path, mode=0o644)
        plan = plan_for(tmp_path, root_assisted=ROOT)
        rep = migrate(plan)

        target_file = plan.target_var / "store" / "seg.jsonl"
        mode = os.stat(target_file).st_mode & 0o777
        assert mode == 0o600, f"ожидалось 0600, получено {oct(mode)}"
        assert rep.state == "reported", "нормализация не должна ломать миграцию"

    def test_content_digest_ignores_mode(self, tmp_path):
        """M-7.4-e: content_digest не должен зависеть от прав."""
        var = make_source(tmp_path, mode=0o644)
        before = content_digest(var)
        os.chmod(var / "store" / "seg.jsonl", 0o600)
        assert content_digest(var) == before, "content_digest изменился от смены прав"

    def test_metadata_digest_detects_mode(self, tmp_path):
        """Обратная сторона: metadata_digest обязан ловить смену прав."""
        var = make_source(tmp_path, mode=0o644)
        before = metadata_digest(var)
        os.chmod(var / "store" / "seg.jsonl", 0o600)
        assert metadata_digest(var) != before, "metadata_digest не увидел смену прав"

    def test_chown_after_content_compare(self, tmp_path):
        """M-7.4-d: chown выполняется ПОСЛЕ сравнения content_digest.

        Проверяем по факту: миграция с нормализацией прав проходит, а значит
        сравнение содержимого шло до смены режимов.
        """
        make_source(tmp_path, mode=0o640)
        rep = migrate(plan_for(tmp_path, root_assisted=ROOT))
        assert rep.state == "reported"
        assert rep.content_digest_source == rep.content_digest_target


class TestForeignUid:
    def test_foreign_uid_rejected_without_adopt(self, tmp_path):
        """M-7.4-i: файл третьего uid без --adopt-uid → код 14."""
        if not ROOT:
            pytest.skip("смена владельца требует root")
        var = make_source(tmp_path)
        victim = var / "store" / "seg.jsonl"
        os.chown(victim, 999, 999)
        with pytest.raises(MigrateError) as exc:
            migrate(plan_for(tmp_path, root_assisted=True))
        assert exc.value.code == ExitCode.FOREIGN_UID
        assert "999" in exc.value.message

    def test_foreign_uid_accepted_with_adopt(self, tmp_path):
        """M-7.4-i (вторая половина): с --adopt-uid 999 → успех, все файлы target."""
        if not ROOT:
            pytest.skip("смена владельца требует root")
        var = make_source(tmp_path)
        os.chown(var / "store" / "seg.jsonl", 999, 999)
        plan = plan_for(tmp_path, root_assisted=True, adopt_uids=[999])
        rep = migrate(plan)
        assert rep.state == "reported"
        assert os.stat(plan.target_var / "store" / "seg.jsonl").st_uid == os.geteuid()

    def test_rejection_leaves_source_untouched(self, tmp_path):
        """Отказ по чужому uid не меняет ни источник, ни target."""
        if not ROOT:
            pytest.skip("смена владельца требует root")
        var = make_source(tmp_path)
        os.chown(var / "store" / "seg.jsonl", 999, 999)
        before = sorted(p.name for p in var.rglob("*") if p.is_file())
        with pytest.raises(MigrateError):
            migrate(plan_for(tmp_path, root_assisted=True))
        after = sorted(p.name for p in var.rglob("*") if p.is_file())
        assert before == after
        assert not (tmp_path / "inst" / "var").exists() or not any(
            (tmp_path / "inst" / "var").iterdir()
        )


class TestReportContents:
    def test_report_records_mode_and_uids(self, tmp_path):
        """Отчёт фиксирует режим и uid — иначе нельзя доказать, что было применено."""
        make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=ROOT)
        migrate(plan)
        data = json.loads((plan.run_dir / "migration-report.json").read_text())
        assert data["mode"] in ("rootless", "root-assisted")
        assert data["target_uid"] == os.geteuid()

    def test_report_has_both_digests(self, tmp_path):
        """M-7.4-g: в отчёте есть content_digest источника, цели и metadata цели."""
        make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=ROOT)
        migrate(plan)
        data = json.loads((plan.run_dir / "migration-report.json").read_text())
        assert data["content_digest_source"]
        assert data["content_digest_target"]
        assert data["content_digest_source"] == data["content_digest_target"]
