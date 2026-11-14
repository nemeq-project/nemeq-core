"""Acceptance and unit tests for the Nemeq wave-2 runtime agents.

Criteria 1-5 from wave2_brief_20261113.md are integration-tested by running
the real agents as subprocesses; helper functions are covered in-process so
the agents/ coverage threshold is meaningful.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "agents"
PYTHON = sys.executable
WATCHED = {"adannu", "capd"}
ALL_AGENTS = {"reu", "adannu", "capd"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _toml_string(value: object) -> str:
    return json.dumps(str(value))


def write_manifest(
    tmp_path: Path,
    interval: float = 1.0,
    event: str = "sleep_requested",
    run_dir: Path | None = None,
) -> Path:
    run_dir = Path(run_dir) if run_dir is not None else tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    modules = {
        "reu": (AGENTS / "reu.py", False),
        "adannu": (AGENTS / "adannu.py", True),
        "capd": (AGENTS / "capd.py", True),
    }
    lines = ["[runtime]", f"run_dir = {_toml_string(run_dir)}", ""]
    for name, (module, watch) in modules.items():
        lines.extend(
            [
                f"[agents.{name}]",
                f"module = {_toml_string(module)}",
                'args = ["run"]',
                f"watch = {'true' if watch else 'false'}",
                "",
            ]
        )
    lines.extend(
        [
            "[schedule]",
            f"interval = {float(interval)}",
            f"event = {_toml_string(event)}",
            "",
            "[capabilities]",
            'known = ["tool:nd"]',
            "",
        ]
    )
    path = tmp_path / "riksu.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def manifest_run_dir(manifest: Path) -> Path:
    with manifest.open("rb") as handle:
        payload = tomllib.load(handle)
    relative = Path(payload["runtime"]["run_dir"])
    return relative if relative.is_absolute() else manifest.parent / relative


def read_json(path: Path, default=None):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_cli(script: str, *args: object, timeout: float = 10.0):
    return subprocess.run(
        [PYTHON, str(AGENTS / script), *[str(item) for item in args]],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


def wait_for(predicate, timeout: float, message: str, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"{message}; last={last!r}")


def init_status(manifest: Path):
    result = run_cli("init.py", "status", "--manifest", manifest)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return None


def reu_status(manifest: Path):
    result = run_cli("reu.py", "status", "--manifest", manifest)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return None


def adannu_status(manifest: Path):
    result = run_cli("adannu.py", "status", "--manifest", manifest)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return None


def wait_init_ready(manifest: Path, timeout: float = 5.0):
    def ready():
        status = init_status(manifest)
        if not status:
            return None
        agents = status.get("agents", [])
        if {agent.get("name") for agent in agents} != ALL_AGENTS:
            return None
        if not all(
            agent.get("state") == "running"
            and pid_alive(agent.get("pid"))
            for agent in agents
        ):
            return None
        return status

    return wait_for(ready, timeout, "init did not report three live agents")


def wait_reu_ready(manifest: Path, status: dict, timeout: float = 5.0):
    expected = {
        agent["name"]: agent["pid"]
        for agent in status["agents"]
        if agent["name"] in WATCHED
    }

    def ready():
        payload = reu_status(manifest)
        if not payload:
            return None
        entries = payload.get("agents", {})
        for name, pid in expected.items():
            entry = entries.get(name)
            if not entry or not entry.get("alive"):
                return None
            if entry.get("pid") != pid:
                return None
        return payload

    return wait_for(ready, timeout, "reu did not adopt watched PIDs")


def start_init(manifest: Path):
    return subprocess.Popen(
        [
            PYTHON,
            str(AGENTS / "init.py"),
            "run",
            "--manifest",
            str(manifest),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )


def start_adannu(manifest: Path):
    return subprocess.Popen(
        [
            PYTHON,
            str(AGENTS / "adannu.py"),
            "run",
            "--manifest",
            str(manifest),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )


def stop_process(process: subprocess.Popen, timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _status_pids(manifest: Path) -> set[int]:
    run_dir = manifest_run_dir(manifest)
    pids: set[int] = set()
    init_payload = read_json(run_dir / "init_status.json", {})
    if isinstance(init_payload, dict):
        for agent in init_payload.get("agents", []):
            pid = agent.get("pid") if isinstance(agent, dict) else None
            if isinstance(pid, int):
                pids.add(pid)
    reu_payload = read_json(run_dir / "reu_status.json", {})
    if isinstance(reu_payload, dict):
        for entry in reu_payload.get("agents", {}).values():
            pid = entry.get("pid") if isinstance(entry, dict) else None
            if isinstance(pid, int):
                pids.add(pid)
    adannu_payload = read_json(run_dir / "adannu_status.json", {})
    if isinstance(adannu_payload, dict) and isinstance(
        adannu_payload.get("pid"), int
    ):
        pids.add(adannu_payload["pid"])
    capd_payload = read_json(run_dir / "capd_status.json", {})
    if isinstance(capd_payload, dict) and isinstance(
        capd_payload.get("pid"), int
    ):
        pids.add(capd_payload["pid"])
    return pids


def cleanup_manifest(manifest: Path) -> None:
    for pid in _status_pids(manifest):
        if pid == os.getpid() or not pid_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@contextlib.contextmanager
def running_init(manifest: Path):
    process = start_init(manifest)
    try:
        yield process
    finally:
        stop_process(process)
        cleanup_manifest(manifest)


@contextlib.contextmanager
def running_adannu(manifest: Path):
    process = start_adannu(manifest)
    try:
        yield process
    finally:
        stop_process(process)
        cleanup_manifest(manifest)


def wait_adannu_ready(manifest: Path, timeout: float = 5.0):
    def ready():
        status = adannu_status(manifest)
        if status and pid_alive(status.get("pid")):
            return status
        return None

    return wait_for(ready, timeout, "adannu status is not ready")


# ---------------------------------------------------------------------------
# Criterion 1: init starts three agents
# ---------------------------------------------------------------------------
def test_init_starts_three_agents(tmp_path):
    manifest = write_manifest(tmp_path, interval=60.0)
    with running_init(manifest) as process:
        status = wait_init_ready(manifest)

        assert status["init_pid"] == process.pid
        assert {agent["name"] for agent in status["agents"]} == ALL_AGENTS
        for agent in status["agents"]:
            assert agent["state"] == "running"
            assert pid_alive(agent["pid"])
        assert (manifest_run_dir(manifest) / "init_status.json").exists()


def test_init_stops_children_on_sigterm(tmp_path):
    manifest = write_manifest(tmp_path, interval=60.0)
    with running_init(manifest) as process:
        status = wait_init_ready(manifest)
        child_pids = [agent["pid"] for agent in status["agents"]]
        process.terminate()
        process.wait(timeout=5)
        wait_for(
            lambda: all(not pid_alive(pid) for pid in child_pids),
            5.0,
            "init did not stop its children on SIGTERM",
        )


# ---------------------------------------------------------------------------
# Criterion 2: reu restarts adannu within 5 seconds
# ---------------------------------------------------------------------------
def test_reu_restarts_adannu_after_sigkill(tmp_path):
    manifest = write_manifest(tmp_path, interval=60.0)
    with running_init(manifest):
        status = wait_init_ready(manifest)
        wait_reu_ready(manifest, status)
        old_pid = next(
            agent["pid"]
            for agent in status["agents"]
            if agent["name"] == "adannu"
        )

        started = time.monotonic()
        os.kill(old_pid, signal.SIGKILL)

        def restarted():
            payload = reu_status(manifest)
            if not payload:
                return None
            entry = payload.get("agents", {}).get("adannu")
            if (
                entry
                and entry.get("alive")
                and entry.get("restarts", 0) >= 1
                and entry.get("pid") != old_pid
                and pid_alive(entry.get("pid"))
            ):
                return entry
            return None

        entry = wait_for(restarted, 5.0, "adannu was not restarted")
        elapsed = time.monotonic() - started
        assert elapsed < 5.0
        assert entry["restarts"] == 1

        def init_sees_new_pid():
            current = init_status(manifest)
            if not current:
                return None
            for agent in current.get("agents", []):
                if (
                    agent.get("name") == "adannu"
                    and agent.get("pid") == entry["pid"]
                    and agent.get("state") == "running"
                    and pid_alive(agent.get("pid"))
                ):
                    return current
            return None

        wait_for(init_sees_new_pid, 5.0, "init status did not show new pid")
        print(f"restart_seconds={elapsed:.3f}", flush=True)


# ---------------------------------------------------------------------------
# Criterion 3: capd denies without grant, allows after grant
# ---------------------------------------------------------------------------
def test_capd_denies_without_grant_and_allows_after_grant(tmp_path):
    run_dir = tmp_path / "capd-run"

    before = run_cli(
        "capd.py", "check", "agent-x", "tool:nd", "--run-dir", run_dir
    )
    assert before.returncode == 1
    assert json.loads(before.stdout)["granted"] is False

    granted = run_cli(
        "capd.py", "grant", "agent-x", "tool:nd", "--run-dir", run_dir
    )
    assert granted.returncode == 0
    assert json.loads(granted.stdout)["changed"] is True

    after = run_cli(
        "capd.py", "check", "agent-x", "tool:nd", "--run-dir", run_dir
    )
    assert after.returncode == 0
    assert json.loads(after.stdout)["granted"] is True

    revoked = run_cli(
        "capd.py", "revoke", "agent-x", "tool:nd", "--run-dir", run_dir
    )
    assert revoked.returncode == 0

    denied_again = run_cli(
        "capd.py", "check", "agent-x", "tool:nd", "--run-dir", run_dir
    )
    assert denied_again.returncode == 1


# ---------------------------------------------------------------------------
# Criterion 4: schedule fires >= 2 times in 3 seconds
# ---------------------------------------------------------------------------
def test_adannu_schedule_fires_sleep_requested(tmp_path):
    manifest = write_manifest(tmp_path, interval=1.0)
    started = time.monotonic()
    with running_adannu(manifest):
        status = wait_adannu_ready(manifest)
        assert status["fired"] == 0
        time.sleep(3.0)
        status = wait_for(
            lambda: (
                current
                if (current := adannu_status(manifest))
                and current.get("fired", 0) >= 2
                else None
            ),
            1.0,
            "schedule did not fire twice",
        )
        elapsed = time.monotonic() - started
        assert elapsed < 5.0
        assert status["fired"] >= 2

        events = read_jsonl(
            manifest_run_dir(manifest) / "adannu_events.jsonl"
        )
        sleep_events = [
            event
            for event in events
            if event.get("event") == "sleep_requested"
        ]
        assert len(sleep_events) >= 2
        assert all(event.get("source") == "schedule" for event in sleep_events)
        assert isinstance(status.get("next_fire"), (int, float))


# ---------------------------------------------------------------------------
# Criterion 5: context overflow never emits sleep_requested
# ---------------------------------------------------------------------------
def test_context_overflow_does_not_emit_sleep_requested(tmp_path):
    manifest = write_manifest(tmp_path, interval=60.0)
    with running_adannu(manifest):
        wait_adannu_ready(manifest)
        notified = run_cli(
            "adannu.py",
            "notify-overflow",
            "--manifest",
            manifest,
        )
        assert notified.returncode == 0
        assert json.loads(notified.stdout)["queued"] is True

        status = wait_for(
            lambda: (
                current
                if (current := adannu_status(manifest))
                and current.get("overflow_events", 0) >= 1
                else None
            ),
            2.0,
            "overflow marker was not consumed",
        )
        time.sleep(0.2)
        events = read_jsonl(
            manifest_run_dir(manifest) / "adannu_events.jsonl"
        )
        assert not any(
            event.get("event") == "sleep_requested" for event in events
        )
        assert status["fired"] == 0


# ---------------------------------------------------------------------------
# Criterion 6: stdlib only, no shell=True
# ---------------------------------------------------------------------------
def test_no_shell_true_and_stdlib_only():
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    for name in ("init.py", "reu.py", "adannu.py", "capd.py"):
        source = (AGENTS / name).read_text(encoding="utf-8")
        assert not re.search(r"shell\s*=\s*True", source), name

        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                if node.module:
                    imported.add(node.module.split(".")[0])
        assert imported, name
        assert imported <= allowed, (name, imported - allowed)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    assert not (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ), name


# ---------------------------------------------------------------------------
# Unit tests for coverage of manifest/state helpers
# ---------------------------------------------------------------------------
def test_manifest_has_required_sections():
    with (ROOT / "riksu.toml").open("rb") as handle:
        manifest = tomllib.load(handle)
    assert list(manifest["agents"]) == ["reu", "adannu", "capd"]
    assert manifest["schedule"]["interval"] == 1.0
    assert manifest["schedule"]["event"] == "sleep_requested"
    assert "capabilities" in manifest
    assert manifest["runtime"]["run_dir"] == "run"


def test_init_manifest_and_status_merge(tmp_path, capsys):
    from agents import init

    manifest_path = write_manifest(tmp_path, interval=2.0)
    manifest = init.load_manifest(manifest_path)
    assert list(manifest["agents"]) == ["reu", "adannu", "capd"]
    assert init.agent_command(
        manifest["agents"]["adannu"], manifest_path.parent
    )[0] == PYTHON

    status = {
        "agents": [
            {"name": "adannu", "pid": 11, "state": "dead"},
            {"name": "capd", "pid": 12, "state": "running"},
        ]
    }
    merged = init.merge_with_reu(
        status,
        {
            "agents": {
                "adannu": {"alive": True, "pid": 21, "restarts": 2}
            }
        },
    )
    assert merged["agents"][0]["pid"] == 21
    assert merged["agents"][0]["state"] == "running"
    assert merged["agents"][0]["restarts"] == 2
    assert merged["agents"][1]["pid"] == 12

    assert init.status_command(tmp_path / "missing-run") == 1
    assert "not_running" in capsys.readouterr().out
    assert init.pid_alive(os.getpid()) is True
    assert init.pid_alive(0) is False


def test_reu_helpers(tmp_path, capsys):
    from agents import reu

    manifest_path = write_manifest(tmp_path)
    manifest = reu.load_manifest(manifest_path)
    watched = reu.watched_specs(manifest)
    assert set(watched) == WATCHED
    assert "reu" not in watched

    records = {
        "adannu": {
            "pid": 42,
            "alive": True,
            "restarts": 1,
            "last_reason": "process_gone",
        }
    }
    payload = reu.build_status(records, os.getpid())
    assert payload["pid"] == os.getpid()
    assert payload["agents"]["adannu"]["restarts"] == 1
    assert reu.pid_alive(os.getpid()) is True
    assert reu.pid_alive(99999999) is False
    assert reu.status_command(tmp_path / "missing-run") == 1
    assert "not found" in capsys.readouterr().err

    init_agents = reu.collect_init_agents(
        {
            "agents": [
                {"name": "adannu", "pid": 123, "state": "running"},
                {"name": "capd", "pid": 124, "state": "running"},
            ]
        }
    )
    assert set(init_agents) == WATCHED

    records = {
        "adannu": {
            "pid": None,
            "alive": False,
            "restarts": 0,
            "last_reason": None,
            "exit_code": None,
            "gone_since": None,
        }
    }
    assert reu.adopt_missing(
        records, init_agents, alive_fn=lambda pid: True
    ) is True
    assert records["adannu"]["pid"] == 123
    assert records["adannu"]["alive"] is True

    reu.refresh_exit_codes(
        records,
        {
            "adannu": {
                "name": "adannu",
                "pid": 123,
                "state": "dead",
                "exit_code": -9,
            }
        },
        {},
    )
    assert records["adannu"]["exit_code"] == -9

    class FakeProcess:
        pid = 321

    records["adannu"]["pid"] = 999
    records["adannu"]["alive"] = True
    restarted: list[str] = []

    def fake_spawn(name: str):
        restarted.append(name)
        return FakeProcess()

    assert reu.restart_dead(
        records,
        watched,
        fake_spawn,
        alive_fn=lambda pid: False,
    ) is True
    assert restarted == ["adannu"]
    assert records["adannu"]["pid"] == 321
    assert records["adannu"]["restarts"] == 1

    records["adannu"]["pid"] = 1000
    assert reu.restart_dead(
        records,
        watched,
        fake_spawn,
        alive_fn=lambda pid: False,
        stopping=True,
    ) is True
    assert records["adannu"]["alive"] is False

    def broken_spawn(name: str):
        raise OSError("no exec")

    records["adannu"]["pid"] = 1001
    records["adannu"]["exit_code"] = -9
    assert reu.restart_dead(
        records,
        watched,
        broken_spawn,
        alive_fn=lambda pid: False,
    ) is True
    assert records["adannu"]["last_reason"].startswith("spawn_failed")

    records["adannu"].update(
        pid=1002,
        alive=True,
        exit_code=0,
        gone_since=time.monotonic(),
    )
    clean_restarts: list[str] = []
    assert reu.restart_dead(
        records,
        watched,
        lambda name: clean_restarts.append(name),
        alive_fn=lambda pid: False,
    ) is True
    assert clean_restarts == []
    assert records["adannu"]["last_reason"] == "clean_exit"
    assert reu.restart_dead(
        records,
        watched,
        lambda name: clean_restarts.append(name),
        alive_fn=lambda pid: False,
    ) is False


def test_reu_run_service_stops_cleanly(tmp_path, monkeypatch, capsys):
    from agents import reu

    class FakeEvent:
        def __init__(self):
            self.flag = False

        def set(self):
            self.flag = True

        def is_set(self):
            return self.flag

        def wait(self, timeout):
            self.flag = True
            return True

    manifest_path = write_manifest(tmp_path, interval=60.0)
    run_dir = manifest_run_dir(manifest_path)
    previous = {
        number: signal.getsignal(number)
        for number in (signal.SIGTERM, signal.SIGINT)
    }
    monkeypatch.setattr(reu.threading, "Event", FakeEvent)
    try:
        assert reu.run_service(manifest_path, run_dir) == 0
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)

    status = read_json(run_dir / "reu_status.json")
    assert status is not None
    assert status["agents"]["adannu"]["alive"] is False
    assert status["agents"]["capd"]["alive"] is False
    assert "[reu] running" in capsys.readouterr().err


def test_adannu_config_and_overflow_helpers(tmp_path, capsys):
    from agents import adannu

    manifest_path = write_manifest(tmp_path, interval=2.5)
    config = adannu.load_config(manifest_path)
    assert config == {"interval": 2.5, "event": "sleep_requested"}

    run_dir = manifest_run_dir(manifest_path)
    state = {"overflow_events": 0}
    assert adannu.consume_overflow(run_dir, state) is False

    assert adannu.notify_overflow(run_dir) == 0
    assert json.loads(capsys.readouterr().out)["queued"] is True
    assert (run_dir / adannu.OVERFLOW_NAME).exists()

    assert adannu.consume_overflow(run_dir, state) is True
    assert state["overflow_events"] == 1
    assert not (run_dir / adannu.OVERFLOW_NAME).exists()

    adannu.append_event(
        run_dir / adannu.EVENTS_NAME,
        {"event": "sleep_requested", "source": "schedule"},
    )
    events = read_jsonl(run_dir / adannu.EVENTS_NAME)
    assert events[0]["event"] == "sleep_requested"

    adannu.write_status(run_dir, {"pid": os.getpid(), "fired": 1})
    assert adannu.read_status(run_dir)["fired"] == 1
    assert adannu.status_command(run_dir) == 0
    assert json.loads(capsys.readouterr().out)["fired"] == 1


def test_capd_state_helpers(tmp_path, capsys):
    from agents import capd

    run_dir = tmp_path / "capd-unit"
    state_path = run_dir / capd.STATE_NAME
    state = capd.load_state(state_path)
    assert state == {"grants": {}}

    assert capd.has_grant(state, "alice", "tool:nd") is False
    assert capd.grant(state, "alice", "tool:nd") is True
    assert capd.grant(state, "alice", "tool:nd") is False
    assert capd.has_grant(state, "alice", "tool:nd") is True
    capd.save_state_atomic(state_path, state)
    assert capd.load_state(state_path)["grants"]["alice"] == ["tool:nd"]

    assert capd.revoke(state, "alice", "tool:nd") is True
    assert capd.revoke(state, "alice", "tool:nd") is False
    assert capd.has_grant(state, "alice", "tool:nd") is False
    capd.save_state_atomic(state_path, state)

    assert capd.main(["check", "alice", "tool:nd", "--run-dir", str(run_dir)]) == 1
    assert json.loads(capsys.readouterr().out)["granted"] is False
    assert capd.main(["grant", "alice", "tool:nd", "--run-dir", str(run_dir)]) == 0
    assert capd.main(["check", "alice", "tool:nd", "--run-dir", str(run_dir)]) == 0
    assert capd.main(["status", "--run-dir", str(run_dir)]) == 1
    assert capd.main(["check", "--run-dir", str(run_dir)]) == 2


def test_capd_service_status(tmp_path):
    run_dir = tmp_path / "capd-service"
    process = subprocess.Popen(
        [
            PYTHON,
            str(AGENTS / "capd.py"),
            "run",
            "--run-dir",
            str(run_dir),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    try:
        status = wait_for(
            lambda: read_json(run_dir / "capd_status.json"),
            5.0,
            "capd status was not written",
        )
        assert status["pid"] == process.pid
        assert status["state"] == "running"
        assert capd_status_rc(run_dir) == 0
    finally:
        stop_process(process)


def capd_status_rc(run_dir: Path) -> int:
    return subprocess.run(
        [
            PYTHON,
            str(AGENTS / "capd.py"),
            "status",
            "--run-dir",
            str(run_dir),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    ).returncode
