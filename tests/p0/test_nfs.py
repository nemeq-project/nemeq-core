"""§9.8 п.6 финалом: РЕАЛЬНАЯ NFS, не mock и не overlay.

История проверки этого пункта:
  1. в контейнере — monkeypatch `_fs_type` («проверено логикой»);
  2. на хосте — overlayfs (реальный mount, но локальная ФС);
  3. здесь — настоящая сетевая ФС по NFS с dsmini12.

Только третий вариант проверяет то, что требует §9.8 п.6: «NFS → код 8»
на живой сетевой файловой системе.

Тест скипается, если NFS-точка не смонтирована: в контейнере это норма,
на хосте с `sudo mount -t nfs 192.168.96.50:/mnt/data/nfs/distr
/mnt/nfs-nemeq` — работает.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from massaru.install import UNSAFE_FS, _InstallError, _fs_type, install, preflight
from massaru.layout import resolve_all

NFS_MOUNT = Path(os.environ.get("NEMEQ_NFS_MOUNT", "/mnt/nfs-nemeq"))


def _real_nfs_available() -> bool:
    """NFS смонтирован И определяется как nfs, а не просто каталог."""
    if not NFS_MOUNT.is_dir() or not os.path.ismount(NFS_MOUNT):
        return False
    try:
        return _fs_type(NFS_MOUNT) in ("nfs", "nfs4")
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _real_nfs_available(),
    reason=(
        "нужна смонтированная NFS: "
        "sudo mount -t nfs 192.168.96.50:/mnt/data/nfs/distr /mnt/nfs-nemeq"
    ),
)


class TestRealNfs:
    """§9.8 п.6: NFS → код 8. Проверка на живой сетевой ФС."""

    def test_kernel_sees_real_nfs(self):
        """Ядро отдаёт тип nfs, а не догадка из имени каталога."""
        assert _fs_type(NFS_MOUNT) in ("nfs", "nfs4")
        assert "nfs" in UNSAFE_FS

    def test_preflight_refuses_real_nfs(self):
        base = NFS_MOUNT / f"nq-pf-{os.getpid()}"
        try:
            r = resolve_all(home=str(base / "home"), instance="alpha", env={})
            pf = preflight(r)
            fs = next(c for c in pf.checks if c["check"] == "filesystem")
            assert fs["ok"] is False, f"NFS не отвергнута: {fs['detail']}"
            assert "nfs" in fs["detail"]
        finally:
            subprocess.run(["rm", "-rf", str(base)], capture_output=True)

    def test_install_code_8_on_real_nfs(self):
        """§7.9: код 8, не 2 — «ФС не годится» отличается от «неверный вызов»."""
        base = NFS_MOUNT / f"nq-inst-{os.getpid()}"
        try:
            r = resolve_all(home=str(base / "home"), instance="alpha", env={})
            with pytest.raises(_InstallError) as exc:
                install(r)
            assert exc.value.code == 8, f"ожидался 8, получен {exc.value.code}"
        finally:
            subprocess.run(["rm", "-rf", str(base)], capture_output=True)

    def test_nothing_written_to_nfs(self):
        """Отказ до записи: на сетевой ФС не остаётся мусора."""
        base = NFS_MOUNT / f"nq-clean-{os.getpid()}"
        try:
            r = resolve_all(home=str(base / "home"), instance="alpha", env={})
            with pytest.raises(_InstallError):
                install(r)
            assert not base.exists(), f"на NFS создано: {base}"
        finally:
            subprocess.run(["rm", "-rf", str(base)], capture_output=True)

    def test_nfs_and_local_differ_by_device(self):
        """§4.4: сверка st_dev отличает сетевую ФС от локальной.

        Локальная точка берётся через tempfile, а не литералом каталога:
        §9.1 запрещает литералы ОС-путей в любом файле, включая тесты
        (слепая зона tests/p0 закрыта).
        """
        import tempfile

        with tempfile.NamedTemporaryFile() as fh:
            local_dir = Path(fh.name).parent
            assert os.stat(NFS_MOUNT).st_dev != os.stat(local_dir).st_dev
