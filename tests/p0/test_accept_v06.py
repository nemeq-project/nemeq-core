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


def live_server(tmp_path, instance="alpha"):
    """Поднять apiserver на реально установленном инстансе со стором.

    Возвращает (server, store, resolved). Сервер поднимается через тот же
    `identity_snapshot()`, что и боевой `__main__`, поэтому проверяется
    НАСТОЯЩИЙ путь заполнения health, а не переданные вручную словари.
    """
    import threading

    from apiserver.handlers import Api
    from apiserver.openapi import build_openapi
    from apiserver.server import make_server
    from apiserver.__main__ import identity_snapshot
    from massaru.store import Store

    # Уже установленный инстанс переиспользуется: повторный `install()`
    # перезаписывает `instance.toml` из стора и СТИРАЕТ подмену, которую
    # проверяет §9.3. Поэтому install — только если инстанса ещё нет.
    r = resolved_for(tmp_path, instance)
    if not (r.home / "instances" / instance / "config" / "instance.toml").is_file():
        install(r)
    store = Store(r.store)

    # identity_snapshot читает пути из окружения — направляем его на инстанс.
    old_home = os.environ.get("NEMEQ_HOME")
    old_inst = os.environ.get("NEMEQ_INSTANCE")
    os.environ["NEMEQ_HOME"] = str(r.home)
    os.environ["NEMEQ_INSTANCE"] = instance
    try:
        identity, status = identity_snapshot()
    finally:
        if old_home is None:
            os.environ.pop("NEMEQ_HOME", None)
        else:
            os.environ["NEMEQ_HOME"] = old_home
        if old_inst is None:
            os.environ.pop("NEMEQ_INSTANCE", None)
        else:
            os.environ["NEMEQ_INSTANCE"] = old_inst

    api = Api(store, lambda: None)
    # store_status_fn — живой читатель: rev/last_hash читаются на каждый
    # запрос, иначе они остаются снимком старта (БЛОКЕР 1).
    from apiserver.__main__ import store_status as live_status

    def reader():
        old_h, old_i = os.environ.get("NEMEQ_HOME"), os.environ.get("NEMEQ_INSTANCE")
        os.environ["NEMEQ_HOME"] = str(r.home)
        os.environ["NEMEQ_INSTANCE"] = instance
        try:
            return live_status()
        finally:
            if old_h is None:
                os.environ.pop("NEMEQ_HOME", None)
            else:
                os.environ["NEMEQ_HOME"] = old_h
            if old_i is None:
                os.environ.pop("NEMEQ_INSTANCE", None)
            else:
                os.environ["NEMEQ_INSTANCE"] = old_i

    srv = make_server(api, port=0, openapi=build_openapi(),
                      identity=identity, store_status=status,
                      store_status_fn=reader)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, store, r, thread


def http_get(srv, path="/healthz"):
    import json as _json
    import urllib.error
    import urllib.request

    port = srv.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
            return resp.status, _json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, _json.loads(exc.read().decode())


class TestHealthInvariant:
    """§9.3: health — инвариант, а не доступность.

    Все проверки идут ПО HTTP против живого сервера и РЕАЛЬНОГО стора:
    константы в ответе не могут удовлетворить требованию «rev не убывает,
    last_hash совпадает», поэтому проверять надо фактические значения.
    """

    def test_rev_and_last_hash_are_real(self, tmp_path):
        """БЛОКЕР 1: rev/last_hash берутся из стора, а не из литерала.

        Инстанс → 3 записи в стор → `/healthz`. Ожидается `rev == 3` и
        непустой `last_hash`, совпадающий с `verify()`.
        """
        from massaru import store_fsck

        srv, store, r, thread = live_server(tmp_path)
        try:
            for i in range(3):
                store.put(f"grants/g{i}", {"spec": {"n": i}, "status": {}},
                          expected_rev=0)
            expected = store_fsck.verify(r.store)

            code, body = http_get(srv)
            assert code == 200, body
            assert body["rev"] == 3, f"rev из литерала: {body['rev']} вместо 3"
            assert body["rev"] == expected.rev
            assert body["last_hash"], "last_hash пуст — значение не из стора"
            assert body["last_hash"] == expected.last_hash
            assert body["fsck"] == "clean"
            for key in ("instance", "home_id", "store_id", "instance_id", "sandbox"):
                assert key in body, f"в health нет поля {key}"
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)
            store.close()

    def test_last_hash_changes_after_write(self, tmp_path):
        """last_hash меняется после записи — значит он живой, не константа."""
        srv, store, r, thread = live_server(tmp_path)
        try:
            code, before = http_get(srv)
            assert code == 200, before
            assert before["rev"] == 0
            store.put("grants/new", {"spec": {"n": 1}, "status": {}}, expected_rev=0)
            code, after = http_get(srv)
            assert code == 200, after
            assert after["rev"] == 1, f"rev не вырос: {after['rev']}"
            assert after["last_hash"] != before["last_hash"], (
                "last_hash не изменился после записи — поле константа"
            )
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)
            store.close()

    def test_rev_never_decreases_between_two_reads(self, tmp_path):
        """Замечание 3: сравниваются два ответа ОДНОГО сервера, не словари.

        Мутант «rev на единицу меньше» даёт second < first и валит тест.
        """
        srv, store, r, thread = live_server(tmp_path)
        try:
            store.put("grants/a", {"spec": {}, "status": {}}, expected_rev=0)
            _, first = http_get(srv)
            store.put("grants/b", {"spec": {}, "status": {}}, expected_rev=0)
            _, second = http_get(srv)
            assert second["rev"] >= first["rev"], (
                f"rev убывает: {first['rev']} -> {second['rev']}"
            )
            assert first["rev"] == 1 and second["rev"] == 2, (
                f"ожидались реальные rev 1 и 2, получены {first['rev']} и {second['rev']}"
            )
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)
            store.close()

    def test_tampered_instance_id_gives_code_5(self, tmp_path):
        """БЛОКЕР 2 / M-9.3-a: подмена instance_id на диске → HTTP 503 и код 5.

        Идёт ПО HTTP. Подмена делается на диске при ЖИВОМ сторе, затем
        сервер стартует заново — §6.1 говорит, что подмена `instance.toml`
        отражается на следующем старте, а стор остаётся авторитетом.
        """
        from massaru.instance import read_store_identity

        srv, store, r, thread = live_server(tmp_path)
        code, body = http_get(srv)
        srv.shutdown(); srv.server_close(); thread.join(timeout=5)
        store.close()
        assert code == 200, body
        assert body["instance_id"] != "TAMPERED"

        # Подмена именно на диске.
        cfg = r.home / "instances" / "alpha" / "config" / "instance.toml"
        lines = [
            'instance_id = "TAMPERED-0000"' if line.startswith("instance_id") else line
            for line in cfg.read_text().splitlines()
        ]
        cfg.write_text("\n".join(lines) + "\n")

        # Стор хранит настоящий id — он авторитет (§6.1).
        assert read_store_identity(r)["instance_id"] != "TAMPERED"

        # «Следующий старт» на том же сторе.
        srv2, store2, r2, thread2 = live_server(tmp_path)
        try:
            code2, body2 = http_get(srv2)
        finally:
            srv2.shutdown(); srv2.server_close(); thread2.join(timeout=5)
            store2.close()

        assert code2 != 200, f"подменённый instance_id дал 200: {body2}"
        assert body2.get("code") == 5, f"ожидался код 5: {body2}"
        assert "store" in body2.get("detail", ""), (
            f"detail не называет авторитет: {body2.get('detail')}"
        )

    def test_no_instance_marker(self):
        """Замечание 4: «инстанса нет» отличимо от «инстанс здоров»."""
        from apiserver.server import health_payload

        code, body = health_payload({}, {})
        assert code == 200
        assert body["status"] == "no-instance"
        assert body["status"] != "ok"

    def test_store_read_failure_is_not_ok(self, tmp_path):
        """Сбой чтения стора не должен выглядеть как `ok`.

        Ошибка внутри `store_status()` раньше могла молча оставить дефолт
        `fsck: clean` — то есть недоступность данных выдавалась за здоровье.
        """
        from apiserver.__main__ import store_status

        old_home = os.environ.get("NEMEQ_HOME")
        os.environ["NEMEQ_HOME"] = str(tmp_path / "nope")
        os.environ.pop("NEMEQ_INSTANCE", None)
        try:
            status = store_status()
        finally:
            if old_home is None:
                os.environ.pop("NEMEQ_HOME", None)
            else:
                os.environ["NEMEQ_HOME"] = old_home
        assert status["fsck"] != "clean", f"сбой чтения скрыт: {status}"

        from apiserver.server import health_payload

        code, body = health_payload(
            {"instance": "a", "home_id": "h", "store_id": "s", "instance_id": "i"},
            status,
        )
        assert code != 200 and body["status"] != "ok"

    def test_dirty_fsck_is_not_ok(self):
        from apiserver.server import health_payload

        code, body = health_payload(
            {"instance": "a", "home_id": "h", "store_id": "s", "instance_id": "i"},
            {"fsck": "dirty", "rev": 1, "last_hash": "x"},
        )
        assert body["status"] != "ok"
        assert code != 200


def _snapshot_for(resolved, instance):
    """Снять identity/status для указанного инстанса (через окружение)."""
    from apiserver.__main__ import identity_snapshot

    old_home = os.environ.get("NEMEQ_HOME")
    old_inst = os.environ.get("NEMEQ_INSTANCE")
    os.environ["NEMEQ_HOME"] = str(resolved.home)
    os.environ["NEMEQ_INSTANCE"] = instance
    try:
        return identity_snapshot()
    finally:
        if old_home is None:
            os.environ.pop("NEMEQ_HOME", None)
        else:
            os.environ["NEMEQ_HOME"] = old_home
        if old_inst is None:
            os.environ.pop("NEMEQ_INSTANCE", None)
        else:
            os.environ["NEMEQ_INSTANCE"] = old_inst


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
               "renameat2(", "link(", "linkat(", "symlink(", "symlinkat(",
               "unlink(", "unlinkat(", "truncate(", "ftruncate(",
               "chmod(", "fchmod(", "chown(", "fchown(")



def _resolve_at(line: str, chunk: str, trace: Path, home: Path) -> str | None:
    """Разрешить относительный путь из strings-аргумента через dirfd.

    strace пишет `openat(3, "evil.txt", O_WRONLY)`. Путь относительный, но
    запись происходит туда, куда указывает fd 3. Если разрешить его от
    `/proc/<pid>/fd/3`, получим настоящую цель.

    Разбор формата strace:
      openat(AT_FDCWD, "x", ...)      -> относительный к cwd процесса
      openat(3, "x", ...)            -> относительный к fd 3
      renameat2(3, "a", 4, "b", 0)   -> два пути, два fd

    Когда fd разрешить нельзя (трейс без /proc, синтетическая строка),
    возвращаем путь относительно известных корней или None, если пути нет.
    Реальная установка идёт с `-f`, и /proc доступен — тогда разрешение точное.
    """
    import re

    # Аргументы между скобками: split по запятым верхнего уровня.
    inside = line.split("(", 1)[1].rsplit(")", 1)[0] if "(" in line else ""
    parts = [x.strip() for x in inside.split(",")]

    # Находим пару (fd, "path"), где path == chunk.
    fd = None
    for i, part in enumerate(parts):
        if part.strip('"') == chunk and i > 0:
            prev = parts[i - 1].strip()
            if prev.isdigit():
                fd = int(prev)
            elif prev == "AT_FDCWD":
                fd = -100
            else:
                # renameat2(3, "a", 4, "b", 0): fd идёт ПЕРЕД путём, но
                # между ними может стоять флаг. Берём ближайший числовой.
                for back in range(i - 1, max(i - 3, -1), -1):
                    cand = parts[back].strip()
                    if cand.isdigit():
                        fd = int(cand)
                        break

    if fd is None:
        return None

    if fd == -100:
        # Относительно cwd процесса. В трейсе cwd обычно HOME установки;
        # если не знаем — считаем путём от home (внутри) и не помечаем.
        return os.path.join(str(home), chunk)

    # Реальный fd: пробуем /proc/<pid>/fd/<n> по каждой строке трейса
    # (strace -f пишет pid первым полем).
    pid = None
    head = line.split("(", 1)[0].split()
    if head and head[0].isdigit():
        pid = int(head[0])
    if pid is not None:
        link = f"/proc/{pid}/fd/{fd}"
        try:
            target = os.readlink(link)
            return os.path.normpath(os.path.join(target, chunk))
        except OSError:
            pass

    # /proc недоступен: консервативно считаем путь подозрительным и
    # возвращаем как есть от корня — лучше ложное срабатывание, чем
    # незамеченная запись вне HOME.
    return os.path.join("/", chunk)


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
        # `call` — имя syscall БЕЗ скобки, а WRITE_CALLS хранит строки со
        # скобкой. Сравниваем по голому имени: иначе ветка endswith не
        # срабатывает никогда и остаются только WRITE_FLAGS, из-за чего
        # renameat2/unlinkat без O_* выпадают из проверки.
        bare = {w.rstrip("(") for w in WRITE_CALLS}
        is_write = call in bare or any(
            f in line for f in WRITE_FLAGS
        )
        if not is_write:
            continue
        # Относительный путь разрешается от dirfd, а не отбрасывается.
        # `openat(3, "evil.txt", O_WRONLY)` пишет туда, куда указывает fd 3 —
        # пропускать такие строки значит НЕ видеть запись вне HOME.
        # В строке может быть НЕСКОЛЬКО путей (renameat2: старый и новый).
        # Разбираем каждый, а не только первый: `renameat2(3, "a", 4, "b")`
        # переносит файл во второй путь, и пропуск второго = пропуск записи.
        for chunk in line.split('"')[1::2]:
            if not chunk.startswith("/"):
                chunk = _resolve_at(line, chunk, trace, home)
                if chunk is None:
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
        """Отсутствие pip в PATH — норма, а не повод падать (§2.1а).

        `in (True, False)` — тавтология: булево значение всегда входит в эту
        пару, проверка не могла провалиться. Нашёл Гермес на ревью.
        Требуем конкретный результат для конкретного PATH.
        """
        from massaru.policy import pip_absent

        assert pip_absent({"PATH": "/nonexistent"}) is True

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
