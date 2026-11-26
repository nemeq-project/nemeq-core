"""Тесты миграции (NEMEQ_HOME_LAYOUT v0.6 §7, мутанты M-7.1/7.2/7.4)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from massaru.migrate import (
    ExitCode,
    MigrateError,
    MigratePlan,
    migrate,
)


def make_source(root: Path) -> Path:
    var = root / "source_var"
    (var / "store" / "log").mkdir(parents=True)
    (var / "store" / "log" / "seg.jsonl").write_text('{"rev":1}\n')
    (var / "epishu" / "e1" / "wal").mkdir(parents=True)
    (var / "epishu" / "e1" / "wal" / "0001.jsonl").write_text('{"a":1}\n')
    (var / "state").mkdir()
    (var / "state" / "s.json").write_text('{"x":1}')
    (var / "data").mkdir()
    (var / "data" / "d.bin").write_bytes(b"\x00\x01\x02")
    (var / "locks").mkdir()
    (var / "locks" / "x.lock").write_text("pid")
    (var / "tmp").mkdir()
    (var / "tmp" / "junk").write_text("staging")
    return var


def plan_for(tmp_path: Path, **kw) -> MigratePlan:
    uid = os.geteuid()
    gid = os.getegid()
    return MigratePlan(
        source_var=kw.get("source", tmp_path / "source_var"),
        target_var=kw.get("target", tmp_path / "inst" / "var"),
        run_dir=kw.get("run_dir", tmp_path / "run"),
        target_uid=kw.get("target_uid", uid),
        target_gid=kw.get("target_gid", gid),
        root_assisted=kw.get("root_assisted", os.geteuid() == 0),
        adopt_uids=kw.get("adopt_uids", []),
    )


class TestHappyPath:
    def test_migrates_content(self, tmp_path):
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)
        rep = migrate(plan)

        assert rep.state == "reported"
        assert (plan.target_var / "epishu" / "e1" / "wal" / "0001.jsonl").is_file()
        assert (plan.target_var / "store" / "log" / "seg.jsonl").is_file()

    def test_locks_and_tmp_not_copied(self, tmp_path):
        # M-6.4-i: locks/ копируется при миграции — тест падает
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)
        migrate(plan)
        assert not (plan.target_var / "locks").exists()
        assert not (plan.target_var / "tmp").exists()

    def test_source_untouched(self, tmp_path):
        # M-7.7-a: источник удаляется после успеха — тест падает
        src = make_source(tmp_path)
        before = sorted(p.name for p in src.iterdir())
        plan = plan_for(tmp_path)
        migrate(plan)
        assert src.is_dir()
        assert sorted(p.name for p in src.iterdir()) == before

    def test_report_written(self, tmp_path):
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)
        migrate(plan)
        rp = plan.run_dir / "migration-report.json"
        assert rp.is_file()
        data = json.loads(rp.read_text())
        assert data["state"] == "reported"
        assert data["content_digest_source"] == data["content_digest_target"]

    def test_state_reported(self, tmp_path):
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)
        migrate(plan)
        st = json.loads((plan.run_dir / "migration.json").read_text())
        assert st["state"] == "reported"


class TestPreflight:
    def test_target_not_empty(self, tmp_path):
        # M-7.1-b: не проверять непустой target — тест падает
        make_source(tmp_path)
        plan = plan_for(tmp_path)
        plan.target_var.mkdir(parents=True)
        (plan.target_var / "junk").write_text("x")
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION

    def test_source_missing(self, tmp_path):
        plan = plan_for(tmp_path)
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION


class TestSymlinks:
    def test_absolute_symlink_rejected(self, tmp_path):
        # M-7.5-a: абсолютный симлинк копируется — тест падает
        src = make_source(tmp_path)
        (src / "epishu" / "e1" / "link").symlink_to("/etc/passwd")
        plan = plan_for(tmp_path)
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.NO_RULE
        assert "/etc/passwd" in exc.value.message

    def test_relative_symlink_preserved(self, tmp_path):
        # M-7.5-b: симлинк разыменовывается — тест падает
        src = make_source(tmp_path)
        (src / "epishu" / "e1" / "rel").symlink_to("wal/0001.jsonl")
        plan = plan_for(tmp_path)
        migrate(plan)
        link = plan.target_var / "epishu" / "e1" / "rel"
        assert link.is_symlink()
        assert os.readlink(link) == "wal/0001.jsonl"


class TestRecovery:
    def test_resume_after_published_without_report(self, tmp_path):
        """kill -9 между rename и отчётом: повторный запуск дописывает отчёт."""
        # M-7.2-a: нет восстановления published — тест падает
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)
        plan.run_dir.mkdir(parents=True)
        plan.target_var.mkdir(parents=True)
        (plan.target_var / "store").mkdir()
        (plan.target_var / "store" / "x").write_text("y")

        from massaru.digest import content_digest
        from massaru.migrate import _write_state

        _write_state(plan.run_dir, {
            "state": "published",
            "staging": "",
            "target": str(plan.target_var),
            "source": str(src),
            "started_at": "2026-11-26T00:00:00Z",
            "mode": "rootless",
            "content_digest": content_digest(plan.target_var),
        })

        rep = migrate(plan)
        assert rep.state == "reported"
        assert (plan.run_dir / "migration-report.json").is_file()

    def test_publishing_with_var_and_staging_is_error(self, tmp_path):
        # M-7.2-c: при обоих каталогах что-то выбирается молча — тест падает
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)
        plan.run_dir.mkdir(parents=True)
        plan.target_var.mkdir(parents=True)
        (plan.target_var / "x").write_text("1")
        staging = plan.target_var.parent / "var.migrating-1"
        staging.mkdir()
        (staging / "y").write_text("2")

        from massaru.migrate import _write_state
        _write_state(plan.run_dir, {
            "state": "publishing",
            "staging": str(staging),
            "target": str(plan.target_var),
            "source": str(src),
        })
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.IO_ERROR

    def test_publishing_neither_exists_is_error(self, tmp_path):
        # M-7.2-e: «нет обоих» не обрабатывается — тест падает
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)
        plan.run_dir.mkdir(parents=True)
        from massaru.migrate import _write_state
        _write_state(plan.run_dir, {
            "state": "publishing",
            "staging": str(tmp_path / "nope"),
            "target": str(plan.target_var),
            "source": str(src),
        })
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.IO_ERROR

    def test_publishing_var_without_staging_but_bad_digest(self, tmp_path):
        # M-7.2-d: манифест не сверяется — тест падает
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)
        plan.run_dir.mkdir(parents=True)
        plan.target_var.mkdir(parents=True)
        (plan.target_var / "corrupt").write_text("x")

        from massaru.migrate import _write_state
        _write_state(plan.run_dir, {
            "state": "publishing",
            "staging": str(tmp_path / "gone"),
            "target": str(plan.target_var),
            "source": str(src),
            "content_digest": "sha256:" + "0" * 64,
        })
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.DATA_DIGEST


class TestDigestGate:
    def test_content_mismatch_detected(self, tmp_path, monkeypatch):
        """Если копирование испортило данные, миграция не публикует."""
        # M-7.4-d: нет сравнения content_digest — тест падает
        src = make_source(tmp_path)
        plan = plan_for(tmp_path)

        import massaru.migrate as m

        real = m._copytree_content

        hits = {"n": 0}

        def corrupt(s, d):
            files, total = real(s, d)
            # _copytree_content вызывается ПО КАТАЛОГУ: для src=.../epishu
            # файл лежит как d/e1/wal/0001.jsonl, без префикса epishu/.
            # Ищем жертву рекурсивно, иначе тест вакуумный.
            for victim in d.rglob("0001.jsonl"):
                victim.write_text('{"a":999}\n')
                hits["n"] += 1
            return files, total

        monkeypatch.setattr(m, "_copytree_content", corrupt)

        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert hits["n"] >= 1, "порча не применилась — тест был бы вакуумным"
        assert exc.value.code == ExitCode.DATA_DIGEST
        # Главное: повреждённое дерево НЕ должно быть опубликовано.
        # Без этой проверки мутант выживал: порчу ловила вторая, пост-публикационная
        # сверка — то есть плохие данные успевали попасть в var/.
        assert not plan.target_var.exists() or not any(plan.target_var.iterdir()), (
            "повреждённое дерево опубликовано — проверка до публикации не работает"
        )
    @pytest.mark.skipif(os.geteuid() != 0, reason="привилегии root не нужны для проверки режимов")
    def test_permissions_normalized(self, tmp_path):
        src = make_source(tmp_path)
        os.chmod(src / "data" / "d.bin", 0o644)
        plan = plan_for(tmp_path)
        migrate(plan)
        mode = os.stat(plan.target_var / "data" / "d.bin").st_mode & 0o777
        assert mode == 0o600

    def test_permissions_normalized_unprivileged(self, tmp_path):
        # M-7.4-f: права не нормализуются — тест падает
        src = make_source(tmp_path)
        os.chmod(src / "state" / "s.json", 0o644)
        plan = plan_for(tmp_path)
        migrate(plan)
        mode = os.stat(plan.target_var / "state" / "s.json").st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"
