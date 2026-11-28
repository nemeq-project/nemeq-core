"""§9.8-c ядром: реальный mount ФС из UNSAFE_FS, без подмены _fs_type.

Раньше эти проверки шли через monkeypatch `_fs_type` — «проверено логикой,
не ядром». На хосте под sudo capabilities полные, overlayfs монтируется
локально, поэтому проверяем реальным определением ФС.

Мутант M-9.8-c (сменить ФС на неподдерживаемую) обязан ловиться этим
тестом на уровне ядра: mount → preflight → отказ с кодом 8.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from massaru.install import UNSAFE_FS, _InstallError, _fs_type, install, preflight
from massaru.layout import resolve_all


def _can_mount_overlay() -> bool:
    """Можно ли смонтировать overlayfs в этой среде."""
    if os.geteuid() != 0:
        return False
    base = Path(tempfile.mkdtemp(prefix="ovl-probe-"))
    for d in ("upper", "work", "merged"):
        (base / d).mkdir()
    try:
        r = subprocess.run(
            ["mount", "-t", "overlay", "overlay",
             "-o", f"lowerdir=/,upperdir={base / 'upper'},workdir={base / 'work'}",
             str(base / "merged")],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            subprocess.run(["umount", str(base / "merged")], capture_output=True)
            return True
        return False
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(base, ignore_errors=True)


pytestmark = pytest.mark.skipif(
    not _can_mount_overlay(),
    reason="нужен root и поддержка overlayfs (в контейнере CapEff=0)",
)


@pytest.fixture
def overlay_mount():
    """Смонтировать overlayfs и отдать точку; размонтировать после."""
    base = Path(tempfile.mkdtemp(prefix="ovl-"))
    for d in ("upper", "work", "merged"):
        (base / d).mkdir()
    r = subprocess.run(
        ["mount", "-t", "overlay", "overlay",
         "-o", f"lowerdir=/,upperdir={base / 'upper'},workdir={base / 'work'}",
         str(base / "merged")],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"overlay не смонтировался: {r.stderr[-100:]}")
    try:
        yield base / "merged"
    finally:
        subprocess.run(["umount", str(base / "merged")], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


class TestRealUnsupportedFs:
    """§9.8 п.6 / §7.9: реальная неподдерживаемая ФС → код 8."""

    def test_overlay_detected_by_kernel(self, overlay_mount):
        """Ядро, а не подмена: _fs_type определяет overlay на реальном mount."""
        assert _fs_type(overlay_mount) == "overlay"
        assert "overlay" in UNSAFE_FS

    def test_preflight_refuses_real_overlay(self, overlay_mount):
        """preflight отказывает на реальной overlay-ФС."""
        home = overlay_mount / "nq-home"
        r = resolve_all(home=str(home), instance="alpha", env={})
        pf = preflight(r)
        fs = next(c for c in pf.checks if c["check"] == "filesystem")
        assert fs["ok"] is False, f"overlay не отвергнут: {fs['detail']}"
        assert "overlay" in fs["detail"]

    def test_install_code_8_on_unsupported_fs(self, overlay_mount):
        """§7.9: код 8, а не 2 — «ФС не годится» отличается от «неверный вызов».

        Это и есть дефект, найденный прогоном на хосте: EXIT_UNSUPPORTED_FS
        был объявлен, но не использовался — отдавался код 2.
        """
        home = overlay_mount / "nq-home2"
        r = resolve_all(home=str(home), instance="alpha", env={})
        with pytest.raises(_InstallError) as exc:
            install(r)
        assert exc.value.code == 8, (
            f"ожидался код 8 (неподдерживаемая ФС), получен {exc.value.code}"
        )

    def test_nothing_created_on_unsupported_fs(self, overlay_mount):
        """Отказ до создания каталогов: на неподходящей ФС ничего не пишется."""
        home = overlay_mount / "nq-home3"
        r = resolve_all(home=str(home), instance="alpha", env={})
        with pytest.raises(_InstallError):
            install(r)
        assert not (home / "instances").exists(), "каталоги созданы до отказа"
