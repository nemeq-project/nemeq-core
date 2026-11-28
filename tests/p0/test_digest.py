"""Тесты store_digest (NEMEQ_HOME_LAYOUT v0.6 §7.3, мутанты M-7.3-*).

Каждый тест соответствует мутанту: убери проверку в коде — тест обязан упасть.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from massaru.digest import (
    DigestError,
    content_digest,
    metadata_digest,
    store_digest,
)


def make_tree(root: Path) -> Path:
    (root / "store").mkdir(parents=True)
    (root / "epishu" / "e1" / "wal").mkdir(parents=True)
    (root / "epishu" / "e1" / "atoms").mkdir(parents=True)
    (root / "epishu" / "e1" / "wal" / "0001.jsonl").write_text('{"a":1}\n')
    (root / "store" / "log").mkdir()
    (root / "store" / "log" / "seg").write_text("x")
    return root


class TestDigestBasics:
    def test_same_tree_same_digest(self, tmp_path):
        a = make_tree(tmp_path / "a")
        b = make_tree(tmp_path / "b")
        assert store_digest(a).digest == store_digest(b).digest

    def test_byte_change_detected(self, tmp_path):
        # M-7.3-b: не хешировать содержимое (только size) — тест падает
        root = make_tree(tmp_path / "t")
        before = store_digest(root).digest
        f = root / "epishu" / "e1" / "atoms" / "a.json"
        f.write_text("same-size")
        before2 = store_digest(root).digest
        f.write_text("diff-size")
        assert store_digest(root).digest != before2
        assert before != before2

    def test_empty_dir_removal_changes_digest(self, tmp_path):
        # M-7.3-a: не включать каталоги — тест падает
        root = make_tree(tmp_path / "t")
        before = store_digest(root).digest
        (root / "epishu" / "e1" / "atoms").rmdir()
        assert store_digest(root).digest != before

    def test_root_mode_is_hashed(self, tmp_path):
        """Корневая директория входит в набор: меняем только её режим.

        Предыдущая версия теста сравнивала пустой стор со стором с одним
        каталогом — они различаются и без корневой записи, поэтому мутант
        выживал. Здесь меняется ЕДИНСТВЕННО режим корня.
        """
        # M-7.3-e: корень не входит в набор — тест падает
        root = tmp_path / "t"
        root.mkdir()
        os.chmod(root, 0o755)
        before = store_digest(root).digest
        os.chmod(root, 0o700)
        after = store_digest(root).digest
        assert before != after, "изменение режима корня обязано менять digest"

    def test_empty_store_vs_one_empty_dir(self, tmp_path):
        a = tmp_path / "empty"
        a.mkdir()
        b = tmp_path / "one"
        b.mkdir()
        (b / "sub").mkdir()
        assert store_digest(a).digest != store_digest(b).digest

    def test_include_root_flag_matters(self, tmp_path):
        root = tmp_path / "t"
        root.mkdir()
        assert store_digest(root).digest != store_digest(root, include_root=False).digest

    def test_dir_mode_changes_digest(self, tmp_path):
        # M-7.3-i: убрать режим из записи каталога — тест падает
        root = make_tree(tmp_path / "t")
        before = store_digest(root).digest
        os.chmod(root / "store" / "log", 0o700)
        assert store_digest(root).digest != before

    def test_order_independence(self, tmp_path):
        # M-7.3-j: не сортировать записи — тест падает на разном порядке создания
        a = tmp_path / "a"
        a.mkdir()
        (a / "z").write_text("1")
        (a / "y").write_text("2")
        b = tmp_path / "b"
        b.mkdir()
        (b / "y").write_text("2")
        (b / "z").write_text("1")
        assert store_digest(a).digest == store_digest(b).digest


class TestSymlinks:
    def test_target_length_prefix_disambiguates(self, tmp_path):
        """Без префикса длины цели `a`+`bc` и `ab`+`c` дали бы один поток.

        Проверяем на уровне кодирования: две записи с разным разбиением
        «длина + цель» обязаны различаться.
        """
        from massaru.digest import _entry_bytes, _u32be, _u64be

        def symlink_entry(rel: str, target: str) -> bytes:
            tb = os.fsencode(target)
            body = _u64be(len(tb)) + tb + _u32be(0o777)
            return _entry_bytes(os.fsencode(rel), 1, body)

        # Одинаковые rel и target -> одинаковые записи (детерминизм)
        assert symlink_entry("x", "abc") == symlink_entry("x", "abc")
        # Разные target -> разные записи
        assert symlink_entry("x", "abc") != symlink_entry("x", "abd")
        # Граница полей защищена длиной: конкатенация без длины склеила бы
        # relpath и target, здесь они разделены.
        assert symlink_entry("ab", "c") != symlink_entry("a", "bc")

        # И на реальном дереве: цель меняется -> digest меняется
        a = tmp_path / "a"
        a.mkdir()
        (a / "x").symlink_to("abc")
        before = store_digest(a).digest
        (a / "x").unlink()
        (a / "x").symlink_to("abd")
        assert store_digest(a).digest != before

    def test_different_targets_differ(self, tmp_path):
        a = tmp_path / "a"
        a.mkdir()
        (a / "x").symlink_to("a")
        b = tmp_path / "b"
        b.mkdir()
        (b / "x").symlink_to("b")
        assert store_digest(a).digest != store_digest(b).digest


class TestRestrictions:
    def test_hardlink_rejected(self, tmp_path):
        # M-7.3-g: разрешить hard links — тест падает
        root = tmp_path / "t"
        root.mkdir()
        (root / "a").write_text("x")
        os.link(root / "a", root / "b")
        with pytest.raises(DigestError, match="hard link"):
            store_digest(root)

    def test_fifo_rejected(self, tmp_path):
        # M-7.3-f: пропускать особые файлы — тест падает
        root = tmp_path / "t"
        root.mkdir()
        os.mkfifo(root / "pipe")
        with pytest.raises(DigestError, match="unsupported"):
            store_digest(root)

    def test_tmp_and_locks_excluded(self, tmp_path):
        # M-7.3-c: не исключать tmp/ и locks/ — тест падает
        root = make_tree(tmp_path / "t")
        before = store_digest(root).digest
        (root / "tmp").mkdir()
        (root / "tmp" / "junk").write_text("staging")
        (root / "locks").mkdir()
        (root / "locks" / "x.lock").write_text("pid")
        assert store_digest(root).digest == before


class TestContentVsMetadata:
    def test_content_ignores_mode(self, tmp_path):
        # M-7.4-e: включить mode в content_digest — тест падает
        a = tmp_path / "a"
        a.mkdir()
        (a / "f").write_text("data")
        os.chmod(a / "f", 0o644)
        b = tmp_path / "b"
        b.mkdir()
        (b / "f").write_text("data")
        os.chmod(b / "f", 0o600)
        assert content_digest(a) == content_digest(b)

    def test_content_detects_data(self, tmp_path):
        a = tmp_path / "a"
        a.mkdir()
        (a / "f").write_text("one")
        b = tmp_path / "b"
        b.mkdir()
        (b / "f").write_text("two")
        assert content_digest(a) != content_digest(b)

    def test_metadata_detects_mode(self, tmp_path):
        a = tmp_path / "a"
        a.mkdir()
        (a / "f").write_text("data")
        os.chmod(a / "f", 0o644)
        b = tmp_path / "b"
        b.mkdir()
        (b / "f").write_text("data")
        os.chmod(b / "f", 0o600)
        assert metadata_digest(a) != metadata_digest(b)

    def test_metadata_detects_owner(self, tmp_path):
        a = tmp_path / "a"
        a.mkdir()
        (a / "f").write_text("data")
        before = metadata_digest(a)
        # смена владельца возможна только root; пропускаем непривилегированно
        if os.geteuid() != 0:
            pytest.skip("chown requires root")
        os.chown(a / "f", 1, 1)
        assert metadata_digest(a) != before


class TestNonUtf8:
    def test_raw_bytes_name_supported(self, tmp_path):
        # M-7.3-k: падать на не-UTF8 имени — тест падает
        root = tmp_path / "t"
        root.mkdir()
        raw = b"\xff\xfe-name"
        path = os.path.join(os.fsencode(str(root)), raw)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(fd, b"x")
        os.close(fd)
        d = store_digest(root)
        assert d.files == 1
