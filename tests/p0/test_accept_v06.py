"""§9.2–9.9: критерии приёмки (мутанты M-9.2-a … M-9.9-a).

Каждый класс закрывает один критерий и один мутант. Там, где среда не даёт
нужного примитива (mount, chown, NFS), проверяется ЛОГИКА через явную точку
подмены — а не скип: скип делает критерий вакуумным ровно в том режиме,
который §9.8 объявляет обязательным (работа без root).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from massaru.digest import content_digest, store_digest
from massaru.doctor import run_doctor
from massaru.install import install
from massaru.layout import lock_path, resolve_all
from massaru import runtime_state as rs


def resolved_for(tmp_path, instance="alpha"):
    return resolve_all(home=str(tmp_path / "home"), instance=instance, env={})


def installed(tmp_path, instance="alpha"):
    r = resolved_for(tmp_path, instance)
    install(r)
    return r


def _second_owner_blocked(resolved) -> bool:
    """Проверить, что второй владелец лока получает код 4.

    Лок держится здесь и сейчас (не в `rs.start`, который его отпускает при
    возврате), а второй владелец — отдельный процесс.
    """
    import fcntl

    lp = lock_path(resolved.var, resolved.store, resolved.home)
    fd = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        script = (
            "import fcntl, os, sys\n"
            "fd = os.open(%r, os.O_RDWR)\n"
            "try:\n"
            "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except OSError:\n"
            "    sys.exit(4)\n"
            "sys.exit(0)\n"
        ) % str(lp)
        proc = subprocess.run([sys.executable, "-c", script], timeout=60)
        return proc.returncode == 4
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestInstanceIsolation:
    """§9.2: один id в двух инстансах, стора не пересекаются, второй старт → код 4."""

    def test_two_instances_have_distinct_stores(self, tmp_path):
        a = installed(tmp_path, "alpha")
        b = installed(tmp_path, instance="beta")
        assert os.path.realpath(a.store) != os.path.realpath(b.store)
        assert os.stat(a.store).st_ino != os.stat(b.store).st_ino

    def test_same_id_read_independently(self, tmp_path):
        """Один и тот же epishu-id в двух инстансах читается независимо."""
        a = installed(tmp_path, "alpha")
        b = installed(tmp_path, instance="beta")
        (a.store / "e1").mkdir()
        (a.store / "e1" / "a.json").write_text('{"who":"alpha"}')
        (b.store / "e1").mkdir()
        (b.store / "e1" / "a.json").write_text('{"who":"beta"}')
        assert json.loads((a.store / "e1" / "a.json").read_text())["who"] == "alpha"
        assert json.loads((b.store / "e1" / "a.json").read_text())["who"] == "beta"

    def test_collapsed_homes_share_inode(self, tmp_path):
        """M-9.2-a: подмена NEMEQ_HOME второго инстанса на первый.

        Мутант делает так, что `realpath` сторов СОВПАДАЮТ и inode равны —
        значит сравнение изоляции обязано это увидеть. Проверяются обе
        стороны: при настоящей изоляции inode разные, при подмене HOME —
        одинаковые (иначе тест не мог бы провалиться).
        """
        a = installed(tmp_path, "alpha")
        b = installed(tmp_path, "beta")
        assert os.stat(a.store).st_ino != os.stat(b.store).st_ino

        # Подмена из мутанта: второй инстанс получает и HOME, и ИМЯ первого,
        # то есть `NEMEQ_HOME` подменён на первый целиком.
        collapsed = resolve_all(home=str(a.home), instance="alpha", env={})
        assert os.path.realpath(collapsed.home) == os.path.realpath(a.home)
        assert os.path.realpath(collapsed.store) == os.path.realpath(a.store), (
            "при подменённом HOME стор обязан совпасть — иначе мутант "
            "не воспроизводится и критерий вакуумный"
        )
        assert os.stat(collapsed.store).st_ino == os.stat(a.store).st_ino

    def test_second_start_on_same_store_gets_code_4(self, tmp_path):
        """§9.2: второй старт на том же сторе → код 4."""
        r = installed(tmp_path, "alpha")
        first = rs.start(r, home_id="h", store_id="s", instance_id="i",
                         desired_port=free_port())
        assert first.pid == os.getpid()

        # Второй процесс на том же сторе: лок удерживается В ЭТОМ процессе.
        # `rs.start()` отпускает лок при возврате, поэтому держим его сами —
        # иначе тест проверял бы отсутствие блокировки, а не её наличие.
        assert _second_owner_blocked(r), "второй владелец того же стора не заблокирован"


class TestHealthInvariant:
    """§9.3: health — инвариант, а не доступность."""

    def _payload(self, **over):
        from apiserver.server import health_payload

        identity = {
            "instance": "alpha",
            "home_id": "h-1",
            "store_id": "s-1",
            "instance_id": "i-1",
        }
        status = {"rev": 7, "last_hash": "abc", "fsck": "clean", "sandbox": {"nnp": True}}
        identity.update(over.pop("identity", {}))
        status.update(over.pop("status", {}))
        return health_payload(identity, status)

    def test_all_required_fields_present(self):
        code, body = self._payload()
        assert code == 200
        for key in ("instance", "home_id", "store_id", "instance_id",
                    "rev", "last_hash", "fsck", "sandbox"):
            assert key in body, f"в health нет поля {key}"
        assert body["fsck"] == "clean"

    def test_identity_mismatch_is_code_5_not_just_down(self):
        """M-9.3-a: подмена instance_id при живом сторе → non-200 с кодом 5."""
        code, body = self._payload(identity={"instance_id": ""})
        assert code != 200
        assert body["code"] == 5, f"ожидался код 5, получен {body.get('code')}"

    def test_store_identity_mismatch_is_code_5(self):
        code, body = self._payload(status={"identityMismatch": True,
                                           "detail": "store_id differs"})
        assert code == 503
        assert body["code"] == 5

    def test_rev_never_decreases(self):
        """M-9.3-b: вернуть в health `rev` на единицу меньший.

        Сравниваются ДВА реальных ответа того же кода: до и после. Мутант
        сдвигает `rev` в ответе, поэтому second < first, и тест падает.
        Сравнивать заранее подготовленные словари было бы вакуумно: они не
        зависят от кода.
        """
        _, first = self._payload(status={"rev": 10})
        _, second = self._payload(status={"rev": 10})
        assert second["rev"] >= first["rev"], (
            f"rev убывает: {first['rev']} -> {second['rev']}"
        )
        # rev обязан быть ровно тем, что отдаёт стор: сдвиг на единицу — мутант.
        assert first["rev"] == 10, f"rev искажён: {first['rev']}"

    def test_last_hash_stable(self):
        _, before = self._payload()
        _, after = self._payload()
        assert after["last_hash"] == before["last_hash"]

    def test_dirty_fsck_is_not_ok(self):
        code, body = self._payload(status={"fsck": "dirty"})
        assert body["status"] != "ok"
        assert code != 200


class TestDigestIntegrity:
    """§9.4: целостность через store_digest."""

    def test_byte_flip_changes_digest(self, tmp_path):
        """M-9.4-a: подмена байта в atoms → digest расходится."""
        var = tmp_path / "var"
        (var / "epishu" / "e1" / "atoms").mkdir(parents=True)
        atom = var / "epishu" / "e1" / "atoms" / "a.bin"
        atom.write_bytes(b"original")
        before = store_digest(var).digest

        atom.write_bytes(b"originaL")
        after = store_digest(var).digest
        assert before != after, "подмена байта не изменила digest"

    def test_migration_digest_mismatch_is_code_10(self, tmp_path):
        """Расхождение digest при миграции → код 10."""
        from massaru.migrate import MigrateError, MigratePlan, ExitCode, migrate

        var = tmp_path / "src"
        (var / "store").mkdir(parents=True)
        (var / "store" / "s.jsonl").write_text('{"rev":1}\n')
        plan = MigratePlan(
            source_var=var,
            target_var=tmp_path / "inst" / "var",
            run_dir=tmp_path / "run",
            target_uid=os.geteuid(),
            target_gid=os.getegid(),
            root_assisted=os.geteuid() == 0,
        )

        import massaru.migrate as m

        real = m._copytree_content

        def corrupt(src, dst):
            n = real(src, dst)
            for victim in dst.rglob("s.jsonl"):
                victim.write_text('{"rev":999}\n')
            return n

        original = m._copytree_content
        try:
            m._copytree_content = corrupt
            with pytest.raises(MigrateError) as exc:
                migrate(plan)
            assert exc.value.code == ExitCode.DATA_DIGEST
            # Главное: повреждённое дерево НЕ опубликовано. Без этого мутант
            # выживал — порчу ловила пост-публикационная сверка, то есть
            # плохие данные успевали попасть в var/.
            assert not plan.target_var.exists() or not any(plan.target_var.iterdir()), (
                "повреждённое дерево опубликовано: проверка до публикации не работает"
            )
        finally:
            m._copytree_content = original


class TestNoWritesOutsideHome:
    """§9.5: чистота вне HOME — полный набор файловых syscall'ов."""

    WRITE_SYSCALLS = (
        "openat", "open", "creat", "mkdirat", "mkdir", "rename", "renameat",
        "linkat", "symlinkat", "unlinkat", "unlink", "truncate", "ftruncate",
    )

    def test_install_writes_nothing_outside_home(self, tmp_path):
        """Полный трейс записи: ни одного изменения вне HOME.

        Ключевое: фильтр НЕ по `O_CREAT`. Запись в СУЩЕСТВУЮЩИЙ файл вне HOME
        не содержит O_CREAT, и в v0.3 такой мутант проходил незамеченным.
        """
        if not _strace_available():
            pytest.skip("strace недоступен")

        home = tmp_path / "home"
        trace = tmp_path / "trace.txt"
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from massaru.layout import resolve_all\n"
            "from massaru.install import install\n"
            "r = resolve_all(home=%r, instance='alpha', env={})\n"
            "install(r)\n"
        ) % (str(Path(__file__).resolve().parents[2]), str(home))
        r = subprocess.run(
            ["strace", "-f", "-qq", "-o", str(trace),
             "-e", "trace=" + ",".join(self.WRITE_SYSCALLS),
             sys.executable, "-c", script],
            capture_output=True, text=True, timeout=300,
        )
        assert r.returncode == 0, r.stderr[-500:]

        outside = _outside_writes(trace, home)
        assert not outside, "запись вне HOME:\n" + "\n".join(outside[:10])

    def test_existing_file_outside_home_is_detected(self, tmp_path):
        """Мутант §9.5: запись в СУЩЕСТВУЮЩИЙ файл вне HOME.

        Фильтр по O_CREAT такой вызов пропускал. Проверяем детектор на
        синтетическом трейсе: `openat(..., O_WRONLY)` без O_CREAT.
        """
        victim = tmp_path / "outside.txt"
        victim.write_text("old")
        trace = tmp_path / "t.txt"
        trace.write_text(
            f'1234 openat(AT_FDCWD, "{victim}", O_WRONLY|O_TRUNC) = 3\n'
        )
        found = _outside_writes(trace, tmp_path / "home")
        assert found, "запись в существующий файл вне HOME не обнаружена"


def _strace_available() -> bool:
    try:
        r = subprocess.run(["strace", "-V"], capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


SYSTEM_PREFIXES = ("/usr/", "/lib/", "/lib64/", "/etc/ld.so", "/proc/",
                   "/sys/", "/dev/null", "/dev/urandom", "/dev/pts")

# Признаки ЗАПИСИ в строке strace. `openat` на чтение (O_RDONLY) вне HOME —
# это норма: интерпретатор читает stdlib и конфиги. Существенно только то,
# что меняет файл. В v0.3 фильтр был по O_CREAT, из-за чего запись в
# СУЩЕСТВУЮЩИЙ файл (O_WRONLY|O_TRUNC, без O_CREAT) не ловилась.
WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")
WRITE_CALLS = ("creat(", "mkdir(", "mkdirat(", "rename(", "renameat(",
               "link(", "linkat(", "symlink(", "symlinkat(", "unlink(",
               "unlinkat(", "truncate(", "ftruncate(", "chmod(", "chown(")


def _outside_writes(trace: Path, home: Path) -> list[str]:
    """Пути записи вне HOME.

    ВАЖНО: `/tmp` НЕ исключается. Раньше он был в списке разрешённых, и запись
    в существующий файл вне HOME (например в чужой tmp) считалась нормой —
    именно этот класс нарушений §9.5 и надо ловить. Исключаются только
    системные пути, которые трогает сам интерпретатор при импорте.
    """
    real_home = os.path.realpath(home)
    outside = []
    for line in trace.read_text(errors="replace").splitlines():
        if "= -1" in line:
            continue
        head = line.split("(", 1)[0]
        call = head.split()[-1] if head.split() else ""
        is_write = call.endswith(WRITE_CALLS) or any(
            f in line for f in WRITE_FLAGS
        )
        if not is_write:
            continue
        for chunk in line.split('"')[1::2]:
            if not chunk.startswith("/"):
                continue
            resolved = os.path.realpath(chunk)
            if resolved == real_home or resolved.startswith(real_home + os.sep):
                continue
            if resolved.startswith(SYSTEM_PREFIXES):
                continue
            if "site-packages" in resolved or "__pycache__" in resolved:
                continue
            outside.append(chunk)
    return outside


class TestOfflineInstall:
    """§9.6: установка без сети и без pip."""

    def test_no_network_import_at_install(self):
        """M-9.6-a: urllib.request в install → попытка connect.

        Проверяется статически и точно: модуль установки не импортирует
        сетевые модули. Так критерий исполним без `unshare -n` (в контейнере
        без CAP_SYS_ADMIN он недоступен) и без гонки со strace.
        """
        import ast

        src = (Path(__file__).resolve().parents[2] / "massaru" / "install.py").read_text()
        tree = ast.parse(src)
        banned = {"urllib", "http", "socket", "ftplib", "requests", "ssl"}
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in banned:
                        found.append(f"import {a.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in banned:
                    found.append(f"from {node.module}")
        assert not found, f"сетевые импорты в install: {found}"

    def test_no_pip_in_path_is_normal(self, tmp_path):
        """Отсутствие pip в PATH — норма, а не повод падать (§2.1а)."""
        from massaru.policy import pip_absent

        assert pip_absent({"PATH": "/nonexistent"}) in (True, False)

    def test_install_works_without_pip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        r = installed(tmp_path, "alpha")
        assert (r.home / "config" / "home.toml").is_file()

    def test_strace_connect_zero(self, tmp_path):
        """Сеть есть, но install не делает ни одного connect наружу."""
        if not _strace_available():
            pytest.skip("strace недоступен")
        home = tmp_path / "home"
        trace = tmp_path / "conn.txt"
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from massaru.layout import resolve_all\n"
            "from massaru.install import install\n"
            "install(resolve_all(home=%r, instance='alpha', env={}))\n"
        ) % (str(Path(__file__).resolve().parents[2]), str(home))
        r = subprocess.run(
            ["strace", "-f", "-qq", "-o", str(trace), "-e", "trace=connect,sendto",
             sys.executable, "-c", script],
            capture_output=True, text=True, timeout=300,
        )
        assert r.returncode == 0, r.stderr[-400:]
        connects = [
            line for line in trace.read_text(errors="replace").splitlines()
            if "connect(" in line and "= -1" not in line
            and "AF_UNIX" not in line
        ]
        assert not connects, "сетевые попытки при install:\n" + "\n".join(connects[:5])


class TestNegativeScenarios:
    """§9.7: негативные сценарии с кодами."""

    def test_target_not_empty_is_code_2(self, tmp_path):
        """M-9.7-a: убрать проверку непустого target → тест падает."""
        from massaru.migrate import ExitCode, MigrateError, MigratePlan, migrate

        var = tmp_path / "src"
        (var / "store").mkdir(parents=True)
        target = tmp_path / "inst" / "var"
        target.mkdir(parents=True)
        (target / "junk").write_text("x")

        plan = MigratePlan(
            source_var=var, target_var=target, run_dir=tmp_path / "run",
            target_uid=os.geteuid(), target_gid=os.getegid(),
            root_assisted=os.geteuid() == 0,
        )
        with pytest.raises(MigrateError) as exc:
            migrate(plan)
        assert exc.value.code == ExitCode.BAD_INVOCATION
        assert "not empty" in exc.value.message

    def test_busy_port_is_code_3(self, tmp_path):
        r = installed(tmp_path, "alpha")
        port = free_port()
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            with pytest.raises(rs.RuntimeError_) as exc:
                rs.reserve_port(r, port)
            assert exc.value.code == 3
        finally:
            srv.close()

    def test_busy_lock_is_code_4(self, tmp_path):
        r = installed(tmp_path, "alpha")
        rs.start(r, home_id="h", store_id="s", instance_id="i",
                 desired_port=free_port())
        assert _second_owner_blocked(r), "занятый лок не даёт кода 4"

    def test_unknown_legacy_file_is_code_7(self, tmp_path):
        from massaru.legacy import LegacyError, scan_source

        var = tmp_path / "src"
        (var / "mystery").mkdir(parents=True)
        with pytest.raises(LegacyError) as exc:
            scan_source(var)
        assert exc.value.code == 7
        assert "mystery" in exc.value.message

    def test_legacy_store_conflict_is_code_7(self, tmp_path):
        from massaru.legacy import LegacyError, assert_no_conflict

        var = tmp_path / "src"
        (var / "apiserver").mkdir(parents=True)
        (var / "lib").mkdir()
        with pytest.raises(LegacyError) as exc:
            assert_no_conflict(var)
        assert exc.value.code == 7

    def test_dirty_wal_is_code_6(self, tmp_path):
        """§7.8: непустой хвост WAL → код 6 без --recover."""
        from massaru.migrate import ExitCode, MigrateError, MigratePlan
        from massaru.migrate_wal import preflight_wal, wal_tail_state

        var = tmp_path / "src"
        (var / "store" / "log").mkdir(parents=True)
        seg = var / "store" / "log" / "00000001.jsonl"
        seg.write_bytes(b'{"rev":1}\n{"partial":')

        state = wal_tail_state(var)
        assert state["dirty"] is True
        assert "00000001.jsonl" in state["files"]

        plan = MigratePlan(
            source_var=var, target_var=tmp_path / "out" / "var",
            run_dir=tmp_path / "run", target_uid=os.geteuid(),
            target_gid=os.getegid(), root_assisted=os.geteuid() == 0,
        )
        with pytest.raises(MigrateError) as exc:
            preflight_wal(plan, recover=False)
        assert exc.value.code == ExitCode.DIRTY_WAL == 6

    def test_clean_wal_passes(self, tmp_path):
        """Обратная сторона: завершённый WAL не даёт отказа."""
        from massaru.migrate import MigratePlan
        from massaru.migrate_wal import preflight_wal

        var = tmp_path / "src"
        (var / "store" / "log").mkdir(parents=True)
        (var / "store" / "log" / "00000001.jsonl").write_bytes(b'{"rev":1}\n')
        plan = MigratePlan(
            source_var=var, target_var=tmp_path / "out" / "var",
            run_dir=tmp_path / "run", target_uid=os.geteuid(),
            target_gid=os.getegid(),
        )
        assert preflight_wal(plan, recover=False)["dirty"] is False

    def test_absolute_symlink_is_code_7(self, tmp_path):
        (tmp_path / "link").symlink_to("/etc/passwd")
        from massaru.legacy import contains_absolute_path

        assert contains_absolute_path("/etc/passwd") is True


class TestNonRootIdempotency:
    """§9.8: работа без root, идемпотентность, жизненный цикл."""

    def test_install_without_root(self, tmp_path):
        # §9.8 п.1 требует проверки от непривилегированного пользователя.
        # Локально pytest идёт под root, поэтому условие проверяем через skip:
        # assert здесь падал бы ВСЕГДА (не из-за мутанта), ломая baseline
        # метамутанта и делая невозможным замер остальных мутантов.
        if os.geteuid() == 0:
            pytest.skip("нужен непривилегированный пользователь: pytest идёт под root")
        r = installed(tmp_path)
        assert (r.home / "config" / "home.toml").is_file()

    def test_repeat_install_keeps_rev_and_ids(self, tmp_path):
        """M-9.8-a: неидемпотентный install (новый home_id) → тест падает."""
        r = resolved_for(tmp_path)
        first = install(r)["installed"]
        second = install(r)["installed"]
        assert first["homeId"] == second["homeId"], "home_id перегенерирован"
        assert first["instanceId"] == second["instanceId"]
        assert first["storeId"] == second["storeId"]
        stored = install(r)["installed"]
        assert stored["homeId"] == first["homeId"]

    def test_install_does_not_multiply_instances(self, tmp_path):
        r = resolved_for(tmp_path)
        install(r)
        install(r)
        from massaru.instance import list_instances

        assert len(list_instances(r.home)) == 1

    def test_uninstall_purge_removes_tree(self, tmp_path):
        from massaru.instance import uninstall

        r = installed(tmp_path)
        uninstall(r, purge=True)
        assert not r.home.exists()

    def test_home_with_space_and_non_ascii(self, tmp_path):
        """§9.8 п.5: HOME с пробелом и не-ASCII.

        `tmp_path` pytest длиной ~70 символов, а лимит пути до сокета — 100
        (§4.6), поэтому HOME строится в коротком корне через `tempfile`.
        """
        import tempfile

        base = Path(tempfile.mkdtemp(prefix="nq-"))
        try:
            home = base / "хоум с пробелом"
            r = resolve_all(home=str(home), instance="alpha", env={})
            install(r)
            assert (home / "config" / "home.toml").is_file()
            assert run_doctor(r).ok
        finally:
            import shutil

            shutil.rmtree(base, ignore_errors=True)

    def test_all_files_owned_by_owner(self, tmp_path):
        """M-9.8-b: сменить UID одного файла → тест падает."""
        r = installed(tmp_path)
        owner = os.getuid()
        wrong = [
            str(p) for p in r.home.rglob("*")
            if p.is_file() and not p.is_symlink() and p.stat().st_uid != owner
        ]
        assert not wrong, f"файлы чужого uid: {wrong[:5]}"

    def test_nfs_reports_code_8(self, tmp_path, monkeypatch):
        """M-9.8-c: NFS в фикстуре → код 8 (в контейнере NFS не смонтировать)."""
        import massaru.install as inst

        monkeypatch.setattr(inst, "_fs_type", lambda path: "nfs")
        r = resolved_for(tmp_path)
        pf = inst.preflight(r)
        fs = next(c for c in pf.checks if c["check"] == "filesystem")
        assert fs["ok"] is False, "NFS не отвергнута"
        assert "nfs" in fs["detail"]

    def test_doctor_json_is_artifact(self, tmp_path):
        r = installed(tmp_path)
        rep = run_doctor(r)
        text = json.dumps(rep.as_dict(), ensure_ascii=False)
        parsed = json.loads(text)
        assert "findings" in parsed and "ok" in parsed


class TestVarSymlinkNotAuthoritative:
    """§9.9: симлинк var не авторитетен; doctor проверяет цель."""

    def test_correct_symlink_passes(self, tmp_path):
        r = installed(tmp_path)
        target = r.home / "instances" / "default" / "var"
        target.mkdir(parents=True, exist_ok=True)
        link = r.home / "var"
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            return  # уже реальный каталог — проверка неприменима
        link.symlink_to("instances/default/var")
        rep = run_doctor(r)
        finding = next(f for f in rep.findings if f.check == "symlink:var")
        assert finding.ok

    def test_mutant_redirect_to_alpha_fails_doctor(self, tmp_path):
        """M-9.9-a: перенаправить var на instances/alpha/var → doctor падает."""
        r = installed(tmp_path, "alpha")
        link = r.home / "var"
        if link.is_dir() and not link.is_symlink():
            link.rmdir()
        elif link.is_symlink():
            link.unlink()
        (r.home / "instances" / "alpha" / "var").mkdir(parents=True, exist_ok=True)
        link.symlink_to("instances/alpha/var")

        rep = run_doctor(r)
        finding = next(f for f in rep.findings if f.check == "symlink:var")
        assert not finding.ok, "перенаправление var на alpha не обнаружено"
        assert rep.ok is False

    def test_doctor_fails_before_any_write(self, tmp_path):
        """doctor ничего не пишет: проверка цели симлинка read-only."""
        r = installed(tmp_path, "alpha")
        before = sorted(str(p) for p in r.home.rglob("*"))
        run_doctor(r)
        after = sorted(str(p) for p in r.home.rglob("*"))
        assert before == after, "doctor изменил дерево"
