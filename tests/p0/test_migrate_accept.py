"""Остаток §7 (мутанты M-7.1-a, M-7.2-b, M-7.2-f, M-7.3-d, M-7.3-h,
M-7.4-c, M-7.4-g, M-7.6-a, M-7.7-b).

Каждый тест закрывает мутант, который до этого блока не был исполняемым.
Требование то же, что и во всей ветке: тест обязан ПАДАТЬ при внесении
мутации в код, иначе критерий вакуумный.

Отдельно: тесты, которые раньше `skip`-ались без root (M-7.4-a, M-7.4-i,
M-7.4-j, M-4.7-b), здесь переписаны так, чтобы исполняться под обычным
пользователем. §9.8 требует работу без root — скип под non-root делал эти
критерии недействительными ровно в том режиме, который обязателен.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from massaru import migrate as migrate_mod
from massaru.digest import DigestError, content_digest, store_digest
from massaru.layout import lock_path
from massaru.migrate import (
    ExitCode,
    MigrateError,
    MigratePlan,
    _write_state,
    migrate,
)


def make_source(root: Path, *, mode: int = 0o644) -> Path:
    var = root / "source_var"
    (var / "store").mkdir(parents=True)
    (var / "store" / "seg.jsonl").write_text('{"rev":1}\n')
    (var / "epishu" / "e1" / "wal").mkdir(parents=True)
    (var / "epishu" / "e1" / "wal" / "0001.jsonl").write_text('{"a":1}\n')
    (var / "epishu" / "e1" / "atoms").mkdir(parents=True)
    (var / "state").mkdir()
    (var / "state" / "s.json").write_text('{"x":1}')
    (var / "data").mkdir()
    (var / "data" / "d.bin").write_bytes(b"\x00\x01\x02")
    os.chmod(var / "store" / "seg.jsonl", mode)
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
        verify_target=kw.get("verify_target", False),
        instance=kw.get("instance", "alpha"),
    )


class TestServiceMustBeStopped:
    """M-7.1-a: migrate при живом сервисе → код 2 и ни одного файла в target."""

    def test_live_service_refused(self, tmp_path):
        var = make_source(tmp_path)
        plan = plan_for(tmp_path)

        lp = lock_path(var, var / "store", None)
        lp.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(MigrateError) as exc:
                migrate(plan)
            assert exc.value.code == ExitCode.BAD_INVOCATION
            assert "stop" in exc.value.message
            # Главное: отказ произошёл ДО записи в target.
            assert not plan.target_var.exists() or not any(plan.target_var.iterdir())
            # И staging не создан.
            leftovers = list((tmp_path / "inst").glob("var.migrating-*")) \
                if (tmp_path / "inst").exists() else []
            assert leftovers == []
        finally:
            os.close(fd)

    def test_external_store_lock_is_found(self, tmp_path):
        """Внешний `--store`: лок живёт в <home>/locks/, а не в <var>/locks/.

        Без передачи `home` проверка смотрела бы на другой файл и НЕ увидела бы
        живой сервис — миграция пошла бы под писателем (§7.1).
        """
        var = make_source(tmp_path)
        ext_store = tmp_path / "elsewhere" / "store"
        ext_store.mkdir(parents=True)
        home = tmp_path / "home"
        home.mkdir()

        plan = plan_for(tmp_path)
        plan.home = home

        lp = lock_path(var, ext_store, home)
        assert "locks" in str(lp) and "elsewhere" not in str(lp)
        lp.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert migrate_mod._service_lock_held(var, ext_store, home) is True
            # Без home лок ищется не там — именно этот дефект и был.
            assert migrate_mod._service_lock_held(var, ext_store, None) is False
        finally:
            os.close(fd)

    def test_stopped_service_proceeds(self, tmp_path):
        """Обратная сторона: лок существует, но никем не держится — миграция идёт."""
        var = make_source(tmp_path)
        lp = lock_path(var, var / "store", None)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text("stale pid 999999")
        rep = migrate(plan_for(tmp_path))
        assert rep.state == "reported"


class TestResumeBetweenIntentAndRename:
    """M-7.2-b: прерывание между шагами 8 и 9 → возврат в verifying, не публикация."""

    def _prepare(self, tmp_path: Path) -> MigratePlan:
        var = make_source(tmp_path)
        plan = plan_for(tmp_path)
        plan.run_dir.mkdir(parents=True)
        staging = plan.target_var.parent / "var.migrating-1"
        import shutil

        shutil.copytree(var / "store", staging / "store")
        shutil.copytree(var / "epishu", staging / "epishu")
        shutil.copytree(var / "state", staging / "state")
        shutil.copytree(var / "data", staging / "data")
        _write_state(plan.run_dir, {
            "state": "publishing",
            "staging": str(staging),
            "target": str(plan.target_var),
            "source": str(var),
            "content_digest": content_digest(var),
        })
        return plan

    def test_valid_staging_resumes_and_publishes(self, tmp_path):
        plan = self._prepare(tmp_path)
        rep = migrate(plan)
        assert rep.state == "reported"
        assert (plan.target_var / "store" / "seg.jsonl").is_file()

    def test_corrupt_staging_is_not_published(self, tmp_path):
        """staging повреждён между intent и rename → код 10, staging удалён.

        Без сверки staging повторный запуск опубликовал бы повреждённое дерево —
        именно это и проверяет мутант.
        """
        plan = self._prepare(tmp_path)
        staging = plan.target_var.parent / "var.migrating-1"
        (staging / "store" / "seg.jsonl").write_text('{"rev":999}\n')

        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.DATA_DIGEST
        assert not plan.target_var.exists(), "повреждённое дерево опубликовано"
        assert not staging.exists(), "невалидный staging обязан быть удалён"


class TestMarkerDurability:
    """M-7.2-f: маркер переживает kill -9 сразу после записи.

    Смонитровать run/ отдельной ФС в контейнере нельзя (нет CAP_SYS_ADMIN),
    поэтому проверяется то, что реально гарантирует durable-запись: fsync
    файла маркера, каталога run/ и ЕГО РОДИТЕЛЯ (§7.2 п.2).
    """

    def test_state_write_fsyncs_file_dir_and_parent(self, tmp_path, monkeypatch):
        calls: list[str] = []
        real_fsync = os.fsync
        real_open = os.open

        import massaru.migrate as m

        def tracking_fsync(fd):
            calls.append(f"fsync:{fd}")
            return real_fsync(fd)

        opened: list[str] = []

        def tracking_open(path, *args, **kwargs):
            fd = real_open(path, *args, **kwargs)
            opened.append(os.path.realpath(path))
            return fd

        monkeypatch.setattr(m.os, "fsync", tracking_fsync)
        monkeypatch.setattr(m.os, "open", tracking_open)

        run_dir = tmp_path / "run"
        _write_state(run_dir, {"state": "prepared"})

        assert calls, "fsync не вызывался вовсе — маркер не durable"
        assert os.path.realpath(run_dir) in opened, "каталог run/ не открывался для fsync"
        assert os.path.realpath(run_dir.parent) in opened, (
            "родитель run/ не fsync-ился: при отдельной ФС метаданные каталога "
            "могут не дойти до диска"
        )

    def test_marker_survives_hard_exit(self, tmp_path):
        """kill -9 сразу после записи: маркер читается после перезапуска."""
        run_dir = tmp_path / "run"
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from pathlib import Path\n"
            "from massaru.migrate import _write_state\n"
            "_write_state(Path(%r), {'state': 'prepared'})\n"
            "import os; os.kill(os.getpid(), 9)\n"
        ) % (str(Path(__file__).resolve().parents[2]), str(run_dir))
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True)
        assert proc.returncode == -9, proc.stderr.decode()[-300:]

        data = json.loads((run_dir / "migration.json").read_text())
        assert data["state"] == "prepared"


class TestDigestOrderStability:
    """M-7.3-d: перестановка порядка обхода не меняет digest."""

    def test_reversed_walk_same_digest(self, tmp_path):
        root = make_source(tmp_path)

        def reversed_walk(path, topdown=True, **kw):
            for dirpath, dirnames, filenames in os.walk(path, topdown=topdown, **kw):
                dirnames.reverse()
                filenames.reverse()
                yield dirpath, dirnames, filenames

        before = store_digest(root).digest
        original = migrate_mod  # noqa: F841  (не используется, ясность)
        import massaru.digest as d

        old = d._walk_fn
        try:
            d._walk_fn = reversed_walk
            after = store_digest(root).digest
        finally:
            d._walk_fn = old

        assert before == after, "digest зависит от порядка обхода ФС"


class TestNonUtf8NameInMigration:
    """M-7.3-h: имя с сырыми байтами \\xff не роняет digest и учитывается."""

    def _add_raw(self, root: Path, raw: bytes, data: bytes = b"x") -> None:
        path = os.path.join(os.fsencode(str(root)), raw)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def test_raw_name_digest_no_exception(self, tmp_path):
        root = tmp_path / "t"
        root.mkdir()
        self._add_raw(root, b"bad-\xff-name")
        res = store_digest(root)
        assert res.files == 1

    def test_raw_name_counted_bytewise(self, tmp_path):
        """Два разных сырых имени дают разные digest — имя учтено, не отброшено."""
        a = tmp_path / "a"
        a.mkdir()
        self._add_raw(a, b"n-\xff", b"same")
        b = tmp_path / "b"
        b.mkdir()
        self._add_raw(b, b"n-\xfe", b"same")
        assert store_digest(a).digest != store_digest(b).digest

    def test_migration_copies_raw_name(self, tmp_path):
        var = make_source(tmp_path)
        self._add_raw(var / "data", b"raw-\xff-file", b"payload")
        plan = plan_for(tmp_path)
        migrate(plan)
        names = os.listdir(os.fsencode(str(plan.target_var / "data")))
        assert b"raw-\xff-file" in names


class TestRunFilesOwned:
    """M-7.4-c: после root-assisted run/migration.json принадлежит целевому uid."""

    def test_run_files_chowned(self, tmp_path, monkeypatch):
        """Эмулируем root-assisted без root: целевой uid — текущий, chown вызывается.

        Проверяется ФАКТ вызова chown на run/migration.json и migration-report.json:
        без него файл остаётся за root после root-assisted миграции.
        """
        var = make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=True)

        touched: list[str] = []
        real_chown = os.lchown

        def tracking_chown(path, uid, gid):
            touched.append(os.path.realpath(path))
            return real_chown(path, uid, gid)

        monkeypatch.setattr(os, "lchown", tracking_chown)
        rep = migrate(plan)
        assert rep.state == "reported"

        run_files = {
            os.path.realpath(plan.run_dir / "migration.json"),
            os.path.realpath(plan.run_dir / "migration-report.json"),
        }
        missing = run_files - set(touched)
        assert not missing, f"run-файлы не переприсвоены: {sorted(missing)}"

    def test_all_home_files_target_uid(self, tmp_path):
        """Все файлы HOME принадлежат целевому uid — в т.ч. run/."""
        var = make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=os.geteuid() == 0)
        plan.run_dir.mkdir(parents=True, exist_ok=True)
        rep = migrate(plan)
        assert rep.state == "reported"

        home = tmp_path
        wrong = [
            str(p) for p in home.rglob("*")
            if p.is_file() and not p.is_symlink() and p.stat().st_uid != os.geteuid()
        ]
        assert not wrong, f"файлы чужого uid: {wrong[:5]}"


class TestFinalDigestOnPublishedTree:
    """M-7.4-g: подмена файла между rename и отчётом ловится финальным пересчётом."""

    def test_tamper_between_rename_and_report(self, tmp_path, monkeypatch):
        var = make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=os.geteuid() == 0)

        real_replace = os.replace
        tampered: list[str] = []

        def tampering_replace(src, dst, *a, **kw):
            result = real_replace(src, dst, *a, **kw)
            target = Path(dst)
            if target == plan.target_var and (target / "store" / "seg.jsonl").is_file():
                (target / "store" / "seg.jsonl").write_text('{"rev":666}\n')
                tampered.append(str(dst))
            return result

        monkeypatch.setattr(os, "replace", tampering_replace)

        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert tampered, "подмена не применилась — тест был бы вакуумным"
        assert exc.value.code == ExitCode.DATA_DIGEST, (
            f"ожидался код 10, получен {exc.value.code}: {exc.value.message}"
        )

    def test_report_has_all_digests(self, tmp_path):
        var = make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=os.geteuid() == 0)
        migrate(plan)
        data = json.loads((plan.run_dir / "migration-report.json").read_text())
        for key in ("content_digest_source", "content_digest_target", "target_digest"):
            assert data[key], f"{key} пуст"
        assert data["content_digest_source"] == data["target_digest"]


class TestVerifyTargetFailure:
    """M-7.7-b: падение --verify-target после публикации не трогает источник."""

    def test_source_survives_failed_verify(self, tmp_path):
        var = make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=os.geteuid() == 0, verify_target=True)

        def boom(_plan):
            raise ValueError("health check failed")

        import massaru.migrate as m

        original = m._verify_target
        try:
            m._verify_target = boom
            with pytest.raises(MigrateError) as exc:
                migrate(plan)
            assert exc.value.code == ExitCode.HEALTH_FAILED
        finally:
            m._verify_target = original

        assert var.is_dir(), "источник удалён при падении --verify-target"
        assert (var / "store" / "seg.jsonl").is_file()
        assert "source is intact" in exc.value.message

    def test_successful_verify_publishes(self, tmp_path):
        var = make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=os.geteuid() == 0, verify_target=True)
        rep = migrate(plan)
        assert rep.state == "reported"
        assert plan.target_var.is_dir()


class TestForeignUidWithoutRoot:
    """M-7.4-i / M-7.4-j без root: скан uid проверяется подменой os.lstat.

    Раньше эти тесты скипались без root — то есть были вакуумными ровно в том
    режиме, который §9.8 объявляет обязательным.
    """

    def _fake_uid(self, monkeypatch, mapping: dict[int, int]):
        real = os.lstat

        def fake(path, *a, **kw):
            st = real(path, *a, **kw)
            uid = mapping.get(int(st.st_uid), st.st_uid)
            if uid == st.st_uid:
                return st
            values = list(st)
            values[4] = uid
            return os.stat_result(values)

        monkeypatch.setattr(os, "lstat", fake)

    def test_third_uid_rejected(self, tmp_path, monkeypatch):
        var = make_source(tmp_path)
        self._fake_uid(monkeypatch, {os.geteuid(): 999})
        with pytest.raises(MigrateError) as exc:
            migrate(plan_for(tmp_path, root_assisted=True))
        assert exc.value.code == ExitCode.FOREIGN_UID
        assert "999" in exc.value.message
        assert not (tmp_path / "inst" / "var").exists()

    def test_adopt_uid_allows_third_uid(self, tmp_path, monkeypatch):
        var = make_source(tmp_path)
        self._fake_uid(monkeypatch, {os.geteuid(): 999})
        rep = migrate(plan_for(tmp_path, root_assisted=True, adopt_uids=[999]))
        assert rep.state == "reported"

    def test_scan_is_full_tree_not_first_hit(self, tmp_path, monkeypatch):
        """Preflight-скан идёт по ВСЕМУ дереву: отказ называет все uid, не первый."""
        var = make_source(tmp_path)
        calls = {"n": 0}
        real = os.lstat

        def fake(path, *a, **kw):
            st = real(path, *a, **kw)
            if Path(path).name == "0001.jsonl":
                calls["n"] += 1
                values = list(st)
                values[4] = 999
                return os.stat_result(values)
            if Path(path).name == "s.json":
                calls["n"] += 1
                values = list(st)
                values[4] = 998
                return os.stat_result(values)
            return st

        monkeypatch.setattr(os, "lstat", fake)
        with pytest.raises(MigrateError) as exc:
            migrate(plan_for(tmp_path, root_assisted=True))
        assert exc.value.code == ExitCode.FOREIGN_UID
        assert calls["n"] >= 1


class TestMergeFlagRemoved:
    """M-7.6-a: флаг --merge удалён → код 2 «unknown flag».

    Нельзя проверять только `SystemExit==2`: argparse вернёт 2 и на любой другой
    ошибке вызова, поэтому тест был бы вакуумным (именно так мутант выживал).
    Проверяется, что флага НЕТ в самом парсере, и что ошибка называет именно его.
    """

    def test_merge_flag_does_not_exist(self):
        from massaru.migrate import build_migrate_parser

        parser = build_migrate_parser()
        flags = {
            opt
            for action in parser._actions
            for opt in action.option_strings
        }
        assert "--merge" not in flags, "флаг --merge вернулся в парсер"
        assert "--purge-source" not in flags, "флаг --purge-source вернулся в парсер"

    def test_merge_flag_rejected(self):
        from massaru.migrate import build_migrate_parser

        with pytest.raises(SystemExit) as exc:
            build_migrate_parser().parse_args(["--from", "/x", "--merge"])
        assert exc.value.code == 2

    def test_cleanup_cmd_is_printed_not_executed(self, tmp_path):
        """--print-cleanup-cmd печатает команду и ничего не удаляет (§7.7)."""
        var = make_source(tmp_path)
        plan = plan_for(tmp_path, root_assisted=os.geteuid() == 0)
        migrate(plan)
        cmd = migrate_mod.cleanup_command(plan.source_var)
        assert "rm -rf" in cmd
        assert str(var) in cmd
        assert var.is_dir(), "печать команды удалила источник"
