"""
Тесты скелета CLI massaru (feature/v1-core).

Только stdlib + pytest; shell=False; tmp_path; параметризация.
Не меняют production-код.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


def run_massaru(
    *args, cwd: Path | None = None, root_dir: Path | None = None
) -> subprocess.CompletedProcess:
    """Запустить ``python -m massaru [--state-root ROOT] <args>``.

    По умолчанию ``--state-root`` берётся из ``root_dir`` (tmp_path).
    Возвращает CompletedProcess.
    """
    cmd = [PYTHON, "-m", "massaru"]
    if root_dir is not None:
        cmd.extend(["--state-root", str(root_dir)])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or REPO,
        shell=False,
    )


def parse_json_output(result):
    """Парсить stdout как JSON; падает с понятным сообщением при ошибке."""
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"stdout не JSON (rc={result.returncode}). "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# -- version --
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_exits_zero(self, tmp_path: Path) -> None:
        """--version: exit 0."""
        result = run_massaru("--version", root_dir=tmp_path)
        assert result.returncode == 0

    def test_version_stdout_contains_massaru(self, tmp_path: Path) -> None:
        """--version: stdout содержит 'massaru'."""
        result = run_massaru("--version", root_dir=tmp_path)
        assert "massaru" in result.stdout


# ---------------------------------------------------------------------------
# -- list --
# ---------------------------------------------------------------------------


class TestList:
    def test_list_missing_root_exits_zero(self, tmp_path: Path) -> None:
        """list при отсутствующем root: exit 0, JSON {"schema":1,"epishu":[]}."""
        root = tmp_path / "state_root"
        result = run_massaru("list", root_dir=root)
        assert result.returncode == 0
        data = parse_json_output(result)
        assert data == {"schema": 1, "epishu": []}

    def test_list_missing_root_does_not_create_state(self, tmp_path: Path) -> None:
        """list при отсутствующем root: root не создаётся."""
        root = tmp_path / "state_root"
        assert not root.exists()
        run_massaru("list", root_dir=root)
        assert not root.exists()

    def test_list_single_running_instance(self, tmp_path: Path) -> None:
        """list: запись с epishu_id, state, path."""
        root = tmp_path / "state_root"
        instance_dir = root / "epishu" / "abc123"
        instance_dir.mkdir(parents=True)
        (instance_dir / "instance.toml").write_text(
            'state = "running"\n', encoding="utf-8"
        )

        result = run_massaru("list", root_dir=root)
        assert result.returncode == 0
        data = parse_json_output(result)
        assert data["schema"] == 1
        epishu_list = data["epishu"]
        assert len(epishu_list) == 1
        record = epishu_list[0]
        assert record["epishu_id"] == "abc123"
        assert record["state"] == "running"
        assert record["path"] == str(root / "epishu" / "abc123")

    def test_list_multiple_instances(self, tmp_path: Path) -> None:
        """list с несколькими инстансами возвращает все записи."""
        root = tmp_path / "state_root"
        for uid in ("aaa", "bbb"):
            (root / "epishu" / uid).mkdir(parents=True)
            (root / "epishu" / uid / "instance.toml").write_text(
                'state = "running"\n', encoding="utf-8"
            )

        result = run_massaru("list", root_dir=root)
        assert result.returncode == 0
        data = parse_json_output(result)
        assert len(data["epishu"]) == 2
        ids = {r["epishu_id"] for r in data["epishu"]}
        assert ids == {"aaa", "bbb"}


# ---------------------------------------------------------------------------
# -- inspect --
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect_existing_instance(self, tmp_path: Path) -> None:
        """inspect <id> для существующего instance.toml: exit 0, JSON."""
        root = tmp_path / "state_root"
        uid = "xyz999"
        instance_dir = root / "epishu" / uid
        instance_dir.mkdir(parents=True)
        (instance_dir / "instance.toml").write_text(
            'state = "stopped"\n', encoding="utf-8"
        )

        result = run_massaru("inspect", uid, root_dir=root)
        assert result.returncode == 0
        data = parse_json_output(result)
        assert data["epishu_id"] == uid
        assert data["path"] == str(instance_dir)
        assert isinstance(data["instance"], dict)
        assert data["instance"]["state"] == "stopped"

    def test_inspect_missing_exits_one(self, tmp_path: Path) -> None:
        """inspect missing: exit 1, JSON status='not_found'."""
        root = tmp_path / "state_root"
        result = run_massaru("inspect", "no_such_id", root_dir=root)
        assert result.returncode == 1
        data = parse_json_output(result)
        assert data["status"] == "not_found"

    def test_inspect_path_escape_exits_two(self, tmp_path: Path) -> None:
        """inspect ../escape: exit 2, JSON содержит error."""
        root = tmp_path / "state_root"
        result = run_massaru("inspect", "../escape", root_dir=root)
        assert result.returncode == 2
        data = parse_json_output(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# -- lifecycle stubs --
# ---------------------------------------------------------------------------


STUB_CASES = [
    ("install", ("zi", "su")),
    ("run", ("epishu-1",)),
    ("sleep", ("epishu-1",)),
    ("stop", ("epishu-1",)),
    ("commit", ("epishu-1",)),
    ("bind_model", ("epishu-1", "model-1")),
]


@pytest.mark.parametrize("command,extra_args", STUB_CASES)
class TestLifecycleStubs:
    def test_lifecycle_stub_exits_twenty(
        self, tmp_path: Path, command: str, extra_args: tuple[str, ...]
    ) -> None:
        """Каждый lifecycle-стуб: exit 20."""
        root = tmp_path / "state_root"
        result = run_massaru(command, *extra_args, root_dir=root)
        assert result.returncode == 20

    def test_lifecycle_stub_json_status(
        self, tmp_path: Path, command: str, extra_args: tuple[str, ...]
    ) -> None:
        """exit 20, JSON status='not_implemented', read_only=true."""
        root = tmp_path / "state_root"
        result = run_massaru(command, *extra_args, root_dir=root)
        data = parse_json_output(result)
        assert data["status"] == "not_implemented"
        assert data["read_only"] is True

    def test_lifecycle_stub_no_side_effects(
        self,
        tmp_path: Path,
        command: str,
        extra_args: tuple[str, ...],
    ) -> None:
        """После вызова под root не появляется файлов/каталогов."""
        root = tmp_path / "state_root"
        assert not root.exists()
        run_massaru(command, *extra_args, root_dir=root)
        assert not root.exists()


# ---------------------------------------------------------------------------
# -- layout_paths --
# ---------------------------------------------------------------------------


class TestLayoutPaths:
    def test_layout_paths_keys(self, tmp_path: Path) -> None:
        """layout_paths(root, 'abc'): ключи wal, zikru-zi, zikru-su, view, state."""
        root = tmp_path / "state_root"
        uid = "abc"
        from massaru.layout import layout_paths

        paths = layout_paths(root, uid)
        assert isinstance(paths, dict)
        assert {
            "wal",
            "zikru-zi",
            "zikru-su",
            "view",
            "state",
        } <= set(paths.keys())

    def test_layout_paths_values_are_paths(self, tmp_path: Path) -> None:
        """layout_paths возвращает Path/str объекты."""
        root = tmp_path / "state_root"
        from massaru.layout import layout_paths

        paths = layout_paths(root, "abc")
        for key, val in paths.items():
            assert isinstance(val, (Path, str)), f"{key}: {type(val).__name__}"


# ---------------------------------------------------------------------------
# -- safe_epishu_id --
# ---------------------------------------------------------------------------


class TestSafeEpishuId:
    def test_safe_id_normal(self) -> None:
        """safe_epishu_id('abc') возвращает 'abc'."""
        from massaru.layout import safe_epishu_id

        assert safe_epishu_id("abc") == "abc"

    @pytest.mark.parametrize(
        "bad_id",
        ["..", "../x", "a/b", "a\\b"],
    )
    def test_safe_id_raises_value_error(self, bad_id: str) -> None:
        """safe_epishu_id с path-traversal бросает ValueError."""
        from massaru.layout import safe_epishu_id

        with pytest.raises(ValueError):
            safe_epishu_id(bad_id)


# ---------------------------------------------------------------------------
# -- stderr human-readable --
# ---------------------------------------------------------------------------


class TestStderr:
    def test_list_stderr_readable(self, tmp_path: Path) -> None:
        """stderr при list -- человекочитаемый текст."""
        root = tmp_path / "state_root"
        result = run_massaru("list", root_dir=root)
        if result.stderr.strip():
            assert isinstance(result.stderr, str)
            assert "\x00" not in result.stderr
