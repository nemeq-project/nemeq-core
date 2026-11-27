"""Тесты поставки зависимостей (v0.6 §2.5, мутанты M-2.5-*)."""
from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest

from massaru.deps import (
    DepsError,
    install_deps,
    read_manifest,
    verify_installed,
    verify_wheelhouse,
)


def make_wheel(path: Path, *, extra: dict[str, bytes] | None = None,
               pth: bool = False, data_dir: bool = False,
               break_record: bool = False) -> Path:
    """Собрать минимальное валидное колесо вручную (без pip)."""
    module = b"VALUE = 42\n"
    metadata = b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n"

    files = {
        "demo/__init__.py": module,
        "demo-1.0.dist-info/METADATA": metadata,
        "demo-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
    }
    if pth:
        files["demo-1.0.pth"] = b"/etc\n"
    if data_dir:
        files["demo-1.0.data/scripts/tool"] = b"#!/bin/sh\n"
    if extra:
        files.update(extra)

    # RECORD со sha256
    import base64

    lines = []
    for name, data in files.items():
        digest = hashlib.sha256(b"" if break_record else data).digest()
        b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        lines.append(f"{name},sha256={b64},{len(data)}")
    lines.append("demo-1.0.dist-info/RECORD,,")
    files["demo-1.0.dist-info/RECORD"] = ("\n".join(lines) + "\n").encode()

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def write_manifest(wheelhouse: Path, wheel: Path, *, wrong: bool = False) -> None:
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if wrong:
        digest = "0" * 64
    (wheelhouse / "manifest.sha256").write_text(
        f"{digest}  {wheel.name}\n", encoding="utf-8"
    )


class TestManifest:
    def test_read_two_formats(self, tmp_path):
        (tmp_path / "manifest.sha256").write_text(
            "abc123  a.whl\nb.whl: def456\n", encoding="utf-8"
        )
        m = read_manifest(tmp_path)
        assert m["a.whl"] == "abc123"
        assert m["b.whl"] == "def456"

    def test_missing_manifest(self, tmp_path):
        with pytest.raises(DepsError) as exc:
            read_manifest(tmp_path)
        assert exc.value.code == 7


class TestHashVerification:
    def test_good_wheel_passes(self, tmp_path):
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl")
        write_manifest(wh, w)
        assert [p.name for p in verify_wheelhouse(wh)] == [w.name]

    def test_tampered_wheel_rejected(self, tmp_path):
        # M-2.5-a: не сверять sha256 — тест падает
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl")
        write_manifest(wh, w, wrong=True)
        with pytest.raises(DepsError) as exc:
            verify_wheelhouse(wh)
        assert exc.value.code == 9

    def test_wheel_not_in_manifest_rejected(self, tmp_path):
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl")
        write_manifest(wh, w)
        make_wheel(wh / "extra-2.0-py3-none-any.whl")
        with pytest.raises(DepsError) as exc:
            verify_wheelhouse(wh)
        assert exc.value.code == 9


class TestFormatCheck:
    def test_pth_rejected(self, tmp_path):
        # M-2.5-b: пропустить .pth — тест падает
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl", pth=True)
        write_manifest(wh, w)
        with pytest.raises(DepsError) as exc:
            install_deps(wh, tmp_path / "target")
        assert exc.value.code == 7
        assert ".pth" in exc.value.message

    def test_data_dir_rejected(self, tmp_path):
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl", data_dir=True)
        write_manifest(wh, w)
        with pytest.raises(DepsError) as exc:
            install_deps(wh, tmp_path / "target")
        assert exc.value.code == 7
        assert ".data" in exc.value.message

    def test_nothing_written_on_rejection(self, tmp_path):
        """Отказ ДО записи: target не должен появиться."""
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl", pth=True)
        write_manifest(wh, w)
        target = tmp_path / "target"
        with pytest.raises(DepsError):
            install_deps(wh, target)
        assert not target.exists(), "каталог не должен создаваться при отказе формата"


class TestRecord:
    def test_broken_record_rejected(self, tmp_path):
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl", break_record=True)
        write_manifest(wh, w)
        with pytest.raises(DepsError) as exc:
            install_deps(wh, tmp_path / "target")
        assert exc.value.code == 9
        assert "RECORD" in exc.value.message


class TestInstall:
    def test_clean_wheel_installs(self, tmp_path):
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl")
        write_manifest(wh, w)
        target = tmp_path / "target"
        rep = install_deps(wh, target)
        assert rep.installed == 1
        assert (target / "demo" / "__init__.py").is_file()
        assert (target / "demo-1.0.dist-info" / "METADATA").is_file()

    def test_bytecode_compiled(self, tmp_path):
        # M-4.3-b: не компилировать .pyc при install — тест падает
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl")
        write_manifest(wh, w)
        target = tmp_path / "target"
        rep = install_deps(wh, target)
        assert rep.compiled >= 1
        assert list(target.rglob("*.pyc")), "должны появиться .pyc"

    def test_permissions_0600(self, tmp_path):
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl")
        write_manifest(wh, w)
        target = tmp_path / "target"
        install_deps(wh, target)
        f = target / "demo" / "__init__.py"
        assert (os.stat(f).st_mode & 0o777) == 0o600

    def test_verify_installed(self, tmp_path):
        wh = tmp_path / "wheelhouse"
        w = make_wheel(wh / "demo-1.0-py3-none-any.whl")
        write_manifest(wh, w)
        target = tmp_path / "target"
        install_deps(wh, target)
        info = verify_installed(target)
        assert "demo-1.0.dist-info" in info["distInfo"]

    def test_verify_empty_target_fails(self, tmp_path):
        target = tmp_path / "empty"
        target.mkdir()
        with pytest.raises(DepsError) as exc:
            verify_installed(target)
        assert exc.value.code == 7
