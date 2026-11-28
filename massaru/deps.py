"""Поставка зависимостей (NEMEQ_HOME_LAYOUT v0.6 §2.5).

Источник истины — `wheelhouse/` + `manifest.sha256`. Никакой сети и никакого
pip: распаковка колёс штатным `zipfile` (§2.3 allow-list).

Порядок строго такой:

  1. сверка sha256 каждого колеса с манифестом        -> код 9 при расхождении
  2. проверка формата: только чистые колёса, без `.pth`
     и без `*.data/`                                   -> код 7 при нарушении
  3. распаковка с сохранением `.dist-info`
  4. сверка `RECORD` по итоговой раскладке             -> код 9
  5. компиляция `.pyc` рядом с модулями (§4.3)

Отказ на шагах 1–2 обязан происходить **до записи** в `lib/pydeps`:
иначе полураспакованное дерево выглядит как установленное.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

EXIT_NO_RULE = 7
EXIT_SUPPLY_HASH = 9

MANIFEST_NAME = "manifest.sha256"
WHEEL_SUFFIX = ".whl"


class DepsError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class DepsReport:
    wheels: list[str] = field(default_factory=list)
    installed: int = 0
    compiled: int = 0
    verified: bool = False

    def as_dict(self) -> dict:
        return {
            "wheels": self.wheels,
            "installed": self.installed,
            "compiled": self.compiled,
            "verified": self.verified,
        }


def read_manifest(wheelhouse: Path) -> dict[str, str]:
    """`manifest.sha256` в формате `sha256  имя.whl` (или `имя: sha256`)."""
    path = wheelhouse / MANIFEST_NAME
    if not path.is_file():
        raise DepsError(EXIT_NO_RULE, f"manifest not found: {path}")
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "  " in line:
            digest, name = line.split("  ", 1)
        elif ": " in line:
            name, digest = line.split(": ", 1)
        else:
            continue
        out[name.strip()] = digest.strip()
    if not out:
        raise DepsError(EXIT_NO_RULE, "manifest is empty")
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_wheelhouse(wheelhouse: Path) -> list[Path]:
    """Шаг 1: сверить sha256 всех колёс с манифестом. Ничего не пишет."""
    manifest = read_manifest(wheelhouse)
    wheels: list[Path] = []

    for name, expected in sorted(manifest.items()):
        path = wheelhouse / name
        if not path.is_file():
            raise DepsError(EXIT_SUPPLY_HASH, f"wheel missing: {name}")
        actual = _sha256_file(path)
        if actual != expected:
            raise DepsError(
                EXIT_SUPPLY_HASH,
                f"hash mismatch for {name}: expected {expected[:16]}…, got {actual[:16]}…",
            )
        wheels.append(path)

    # Колёса без записи в манифесте — тоже нарушение целостности поставки.
    extra = sorted(
        p.name for p in wheelhouse.iterdir()
        if p.is_file() and p.suffix == WHEEL_SUFFIX and p.name not in manifest
    )
    if extra:
        raise DepsError(EXIT_SUPPLY_HASH, f"wheels not in manifest: {', '.join(extra)}")

    return wheels


def check_wheel_format(wheel: Path) -> None:
    """Шаг 2: только чистые колёса. `.pth` и `*.data/` — отказ ДО записи."""
    try:
        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise DepsError(EXIT_NO_RULE, f"not a valid wheel {wheel.name}: {exc}") from exc

    bad_pth = [n for n in names if n.endswith(".pth")]
    if bad_pth:
        raise DepsError(
            EXIT_NO_RULE,
            f"{wheel.name}: .pth files are not supported ({', '.join(bad_pth[:3])})",
        )

    bad_data = [n for n in names if ".data/" in n]
    if bad_data:
        raise DepsError(
            EXIT_NO_RULE,
            f"{wheel.name}: *.data/ layout is not supported ({', '.join(bad_data[:3])})",
        )

    if not any(n.endswith(".dist-info/METADATA") for n in names):
        raise DepsError(EXIT_NO_RULE, f"{wheel.name}: no .dist-info/METADATA")


def _record_hashes(zf: zipfile.ZipFile) -> dict[str, str]:
    """`RECORD` колеса: отображает путь -> ожидаемый sha256 (base64-не нужен).

    Берём хеши из RECORD и сверяем с фактическим содержимым архива.
    """
    record_name = next(
        (n for n in zf.namelist() if n.endswith(".dist-info/RECORD")), None
    )
    if record_name is None:
        return {}

    import base64

    out: dict[str, str] = {}
    text = zf.read(record_name).decode("utf-8")
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or not parts[1]:
            continue
        path, digest = parts[0], parts[1]
        if not digest.startswith("sha256="):
            continue
        try:
            out[path] = base64.urlsafe_b64decode(digest[7:] + "==").hex()
        except Exception:
            continue
    return out


def verify_record(wheel: Path) -> int:
    """Шаг 4 (до распаковки): сверить содержимое архива с его `RECORD`.

    Проверяем внутри архива — так расхождение обнаруживается до записи.
    Возвращает число проверенных файлов.
    """
    checked = 0
    with zipfile.ZipFile(wheel) as zf:
        expected = _record_hashes(zf)
        for name, want in expected.items():
            try:
                data = zf.read(name)
            except KeyError:
                raise DepsError(
                    EXIT_SUPPLY_HASH, f"{wheel.name}: RECORD lists missing file {name}"
                ) from None
            got = hashlib.sha256(data).hexdigest()
            if got != want:
                raise DepsError(
                    EXIT_SUPPLY_HASH,
                    f"{wheel.name}: RECORD mismatch for {name}",
                )
            checked += 1
    return checked


def install_deps(wheelhouse: Path, target: Path, *, force: bool = False) -> DepsReport:
    """Полный цикл: проверить всё, потом распаковать, потом скомпилировать."""
    report = DepsReport()

    # 1–2: проверки ДО любой записи (иначе полуустановка выглядит целой)
    wheels = verify_wheelhouse(wheelhouse)
    for w in wheels:
        check_wheel_format(w)
        verify_record(w)

    if target.exists() and any(target.iterdir()) and not force:
        raise DepsError(
            EXIT_NO_RULE, f"target is not empty: {target} (pass force=True to overwrite)"
        )

    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o700)

    # 3: распаковка с сохранением .dist-info
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                dest = target / info.filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    out.write(src.read())
                os.chmod(dest, 0o600)
        report.wheels.append(wheel.name)
        report.installed += 1

    # 5: байткод компилируется здесь же, дальше lib/ только читается (§4.3)
    report.compiled = _compile(target)
    report.verified = True
    return report


def _compile(target: Path) -> int:
    import compileall

    compileall.compile_dir(str(target), quiet=2, force=True, legacy=False)
    return sum(1 for _ in target.rglob("*.pyc"))


def verify_installed(target: Path) -> dict:
    """Проверка установленного дерева: `.dist-info` на месте, импорт возможен."""
    if not target.is_dir():
        raise DepsError(EXIT_NO_RULE, f"not a directory: {target}")

    dist_infos = sorted(p.name for p in target.iterdir() if p.name.endswith(".dist-info"))
    if not dist_infos:
        raise DepsError(EXIT_NO_RULE, "no .dist-info in target — install incomplete")

    pyc = sum(1 for _ in target.rglob("*.pyc"))
    return {"distInfo": dist_infos, "pyc": pyc, "target": str(target)}


def run_cli_deps(
    action: str,
    *,
    wheelhouse: str,
    target: str,
    force: bool = False,
) -> tuple[int, dict]:
    """Слой для `nqctl deps install|verify|format-check`."""
    wh = Path(wheelhouse)
    tg = Path(target)
    try:
        if action == "format-check":
            wheels = verify_wheelhouse(wh)
            for w in wheels:
                check_wheel_format(w)
                verify_record(w)
            return 0, {"ok": True, "wheels": [w.name for w in wheels]}

        if action == "verify":
            return 0, verify_installed(tg)

        if action == "install":
            return 0, install_deps(wh, tg, force=force).as_dict()

        return EXIT_NO_RULE, {"title": "Invalid", "detail": f"unknown action {action!r}"}
    except DepsError as exc:
        return exc.code, {"title": "DepsFailed", "status": exc.code, "detail": exc.message}
