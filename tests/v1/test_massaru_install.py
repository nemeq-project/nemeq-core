"""Тесты среза massaru v1 (install / verify). 3 теста, helper, pytest+stdlib."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
REPO = Path(__file__).resolve().parents[2]


def run_cli(*args, root_dir: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [PYTHON, "-m", "massaru"]
    if root_dir is not None:
        cmd.extend(["--state-root", str(root_dir)])
    cmd.extend(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=REPO, shell=False
    )


def make_image(tmp_dir: Path, name: str, files: dict[str, str],
               kind: str = "zi", extra: dict | None = None) -> tuple[Path, str]:
    d = tmp_dir / name
    d.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        (d / rel).write_text(body, encoding="utf-8")
    from massaru.images import image_digest
    digest = image_digest(d)
    manifest = {"kind": kind, "name": name, "version": "1.0", "digest": digest}
    if extra:
        manifest.update(extra)
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d, digest


class TestInstall:
    """install: exit 0, instance.state=dormant, zi_digest/su_digest верны."""

    def test_install_creates_layout(self, tmp_path: Path) -> None:
        zi, zi_d = make_image(tmp_path, "zi1", {"data.bin": "ok"}, kind="zi")
        su, su_d = make_image(tmp_path, "su1", {"cfg.toml": "x"}, kind="su",
                              extra={"endpoint_class": "opus"})
        r = run_cli("install", str(zi), str(su), "--epishu-id", "ep1", root_dir=tmp_path)
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["status"] == "installed"
        import tomllib
        with (tmp_path / "epishu" / "ep1" / "instance.toml").open("rb") as fh:
            inst = tomllib.load(fh)
        assert inst["state"] == "dormant"
        assert inst["zi_digest"] == zi_d
        assert inst["su_digest"] == su_d


class TestDigestMismatch:
    """install с неверным digest: exit 1, code=digest_mismatch."""

    BAD = "sha256:" + "0" * 64

    def test_bad_digest(self, tmp_path: Path) -> None:
        zi = make_image(tmp_path, "zi_bad", {"f": "x"}, kind="zi")[0]
        su = make_image(tmp_path, "su_bad", {"f": "x"}, kind="su",
                        extra={"endpoint_class": "opus"})[0]
        # подменяем digest в manifest.json
        for d in (zi, su):
            m = json.loads((d / "manifest.json").read_text())
            m["digest"] = self.BAD
            (d / "manifest.json").write_text(json.dumps(m))
        r = run_cli("install", str(zi), str(su), "--epishu-id", "ep1", root_dir=tmp_path)
        assert r.returncode == 1
        data = json.loads(r.stdout)
        assert data["code"] == "digest_mismatch"
        assert not (tmp_path / "epishu" / "ep1").exists()


class TestVerifyAfterTamper:
    """install, изменить файл в zi, verify <id> -> exit 1 code=digest_mismatch."""

    def test_verify_detects_tamper(self, tmp_path: Path) -> None:
        zi, _ = make_image(tmp_path, "zi1", {"data.bin": "ok"}, kind="zi")
        su, _ = make_image(tmp_path, "su1", {"cfg.toml": "x"}, kind="su",
                           extra={"endpoint_class": "opus"})
        run_cli("install", str(zi), str(su), "--epishu-id", "ep1", root_dir=tmp_path)
        # tamper
        (zi / "data.bin").write_text("tampered", encoding="utf-8")
        r = run_cli("verify", "ep1", root_dir=tmp_path)
        assert r.returncode == 1
        data = json.loads(r.stdout)
        assert data["code"] == "digest_mismatch"

        import tomllib

        bind = run_cli("bind_model", "ep1", "should-not-bind", root_dir=tmp_path)
        assert bind.returncode == 1
        assert json.loads(bind.stdout)["code"] == "digest_mismatch"
        with (
            tmp_path / "epishu" / "ep1" / "instance.toml"
        ).open("rb") as fh:
            assert tomllib.load(fh)["model"] == ""


class TestLayoutAndBind:
    def test_install_creates_full_layout(self, tmp_path: Path) -> None:
        from massaru.layout import STANDARD_DIRS

        zi, _ = make_image(tmp_path, "zi_layout", {"a": "1"}, kind="zi")
        su, _ = make_image(tmp_path, "su_layout", {"b": "2"}, kind="su",
                           extra={"endpoint_class": "opus"})
        assert run_cli(
            "install", str(zi), str(su), "--epishu-id", "ep-l",
            root_dir=tmp_path,
        ).returncode == 0
        base = tmp_path / "epishu" / "ep-l"
        for name in STANDARD_DIRS:
            assert (base / name).is_dir(), name
        for name in ("instance.toml", "tush.toml", "claims.jsonl",
                     "settlement.jsonl"):
            assert (base / name).is_file(), name

    def test_bind_model_updates_only_model(self, tmp_path: Path) -> None:
        import tomllib

        zi, zi_d = make_image(tmp_path, "zi_b", {"a": "1"}, kind="zi")
        su, su_d = make_image(tmp_path, "su_b", {"b": "2"}, kind="su",
                              extra={"endpoint_class": "sonnet"})
        assert run_cli(
            "install", str(zi), str(su), "--epishu-id", "ep-b",
            root_dir=tmp_path,
        ).returncode == 0
        tush_path = tmp_path / "epishu" / "ep-b" / "tush.toml"
        tush_before = tush_path.read_bytes()
        result = run_cli("bind_model", "ep-b", "sonnet-5", root_dir=tmp_path)
        assert result.returncode == 0
        assert json.loads(result.stdout)["model"] == "sonnet-5"
        with (tmp_path / "epishu" / "ep-b" / "instance.toml").open("rb") as fh:
            inst = tomllib.load(fh)
        assert inst["model"] == "sonnet-5"
        assert inst["zi_digest"] == zi_d
        assert inst["su_digest"] == su_d
        assert inst["state"] == "dormant"
        assert inst["endpoint_class"] == "sonnet"
        assert tush_path.read_bytes() == tush_before

    def test_bind_model_unknown(self, tmp_path: Path) -> None:
        result = run_cli("bind_model", "no-such", "model", root_dir=tmp_path)
        assert result.returncode == 1
        assert json.loads(result.stdout)["status"] == "not_found"

    def test_install_rejects_unsafe_id(self, tmp_path: Path) -> None:
        zi, _ = make_image(tmp_path, "zi_t", {"a": "1"}, kind="zi")
        su, _ = make_image(tmp_path, "su_t", {"b": "2"}, kind="su",
                           extra={"endpoint_class": "opus"})
        root = tmp_path / "state"
        result = run_cli(
            "install", str(zi), str(su), "--epishu-id", "../escape",
            root_dir=root,
        )
        assert result.returncode != 0
        assert not (root / "epishu").exists()
        assert not (tmp_path / "escape").exists()

    def test_one_epishu_end_to_end(self, tmp_path: Path) -> None:
        import tomllib

        zi, _ = make_image(tmp_path, "zi_e2e", {"a": "1"}, kind="zi")
        su, _ = make_image(tmp_path, "su_e2e", {"b": "2"}, kind="su",
                           extra={"endpoint_class": "gpt"})
        installed = run_cli(
            "install", str(zi), str(su), "--epishu-id", "ep-e2e",
            root_dir=tmp_path,
        )
        assert installed.returncode == 0
        assert run_cli(
            "bind_model", "ep-e2e", "gpt-5", root_dir=tmp_path
        ).returncode == 0

        listed = json.loads(
            run_cli("list", root_dir=tmp_path).stdout
        )
        assert [entry["epishu_id"] for entry in listed["epishu"]] == ["ep-e2e"]
        inspected = json.loads(
            run_cli("inspect", "ep-e2e", root_dir=tmp_path).stdout
        )
        assert inspected["instance"]["state"] == "dormant"
        assert inspected["instance"]["model"] == "gpt-5"
        with (
            tmp_path / "epishu" / "ep-e2e" / "instance.toml"
        ).open("rb") as fh:
            assert tomllib.load(fh)["model"] == "gpt-5"

        # run is still a lifecycle stub: exit 20, no process/files.
        before = sorted(p.name for p in (tmp_path / "epishu" / "ep-e2e").iterdir())
        result = run_cli("run", "ep-e2e", root_dir=tmp_path)
        assert result.returncode == 20
        after = sorted(p.name for p in (tmp_path / "epishu" / "ep-e2e").iterdir())
        assert before == after


class TestImageDigest:
    def test_known_vector(self, tmp_path: Path) -> None:
        from massaru.images import image_digest

        image = tmp_path / "img"
        image.mkdir()
        (image / "a.txt").write_text("abc", encoding="utf-8")
        assert image_digest(image) == (
            "sha256:79d91ad7d03cf530166259c13b5a0019e9b6a3c363df04f182d5685a6191c398"
        )

    def test_tamper_changes_digest(self, tmp_path: Path) -> None:
        from massaru.images import image_digest

        image = tmp_path / "img"
        image.mkdir()
        (image / "a.txt").write_text("abc", encoding="utf-8")
        first = image_digest(image)
        (image / "a.txt").write_text("abcd", encoding="utf-8")
        assert image_digest(image) != first

    def test_symlink_rejected(self, tmp_path: Path) -> None:
        from massaru.images import ImageError, image_digest

        image = tmp_path / "img_symlink"
        image.mkdir()
        target = tmp_path / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        (image / "link.txt").symlink_to(target)
        with pytest.raises(ImageError):
            image_digest(image)
