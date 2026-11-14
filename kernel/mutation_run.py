#!/usr/bin/env python3
"""Mutation run for kernel/libbu.py (SDD v1.3.1, rule of §3).

Every entry is a wrong implementation of exactly one requirement.  Each
mutant is copied into an isolated temporary tree together with
``test_libbu.py`` and the acceptance suite is executed; the run succeeds only
when all mutants are caught.

Usage (from /home/daedalus/work/nemeq)::

    python3 kernel/mutation_run.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIBBU = HERE / "libbu.py"
TESTS = HERE / "test_libbu.py"
INIT = HERE / "__init__.py"

MUTANTS: list[dict[str, str]] = [
    {
        "id": "M01",
        "requirement": "T-00",
        "name": "constructor_accepts_nonpositive_budget",
        "old": """        if budget <= 0:
            raise ValueError("budget must be positive")""",
        "new": """        if False:
            raise ValueError("budget must be positive")""",
    },
    {
        "id": "M02",
        "requirement": "T-01",
        "name": "session_loop_dispatch_without_its_session",
        "old": """                    dispatched = self.dispatch(
                        {"name": name, "arguments": arguments},
                        active_session_id,
                    )""",
        "new": """                    dispatched = self.dispatch(
                        {"name": name, "arguments": arguments},
                        self.open_session(agent_id),
                    )""",
    },
    {
        "id": "M03",
        "requirement": "T-01",
        "name": "session_loop_does_not_close_session",
        "old": """        finally:
            self.close_session(active_session_id)""",
        "new": """        finally:
            pass""",
    },
    {
        "id": "M04",
        "requirement": "T-02",
        "name": "max_turns_returns_text_instead_of_refusing",
        "old": """            raise MaxTurnsExceeded(
                f"max turns exceeded: {turns}", turns=turns
            )""",
        "new": """            return {
                "text": "",
                "turns": turns,
                "tokens_in": None if tokens_in_missing else tokens_in,
                "tokens_out": None if tokens_out_missing else tokens_out,
            }""",
    },
    {
        "id": "M05",
        "requirement": "T-03",
        "name": "llm_fn_gets_empty_messages_each_turn",
        "old": """                    reply = llm(messages_for_llm, tools)""",
        "new": """                    reply = llm([], tools)""",
    },
    {
        "id": "M06",
        "requirement": "T-03",
        "name": "llm_fn_gets_history_by_reference",
        "old": """                messages_for_llm = copy.deepcopy(history)""",
        "new": """                messages_for_llm = history""",
    },
    {
        "id": "M07",
        "requirement": "T-03",
        "name": "llm_tools_empty",
        "old": """                tools = sorted(self._tools)""",
        "new": """                tools = []""",
    },
    {
        "id": "M08",
        "requirement": "T-03",
        "name": "usage_only_response_treated_as_text",
        "old": """                raise LLMError(
                    "llm_fn returned an unknown response type",
                    turns=turns,
                    response_type=repr(reply_type),
                )""",
        "new": """                if reply_type == "usage":
                    turns += 1
                    return {
                        "text": "",
                        "turns": turns,
                        "tokens_in": None if tokens_in_missing else tokens_in,
                        "tokens_out": None if tokens_out_missing else tokens_out,
                    }
                raise LLMError(
                    "llm_fn returned an unknown response type",
                    turns=turns,
                    response_type=repr(reply_type),
                )""",
    },
    {
        "id": "M09",
        "requirement": "T-04",
        "name": "context_add_does_not_recalculate_used_on_replace",
        "old": """            new_used = self._used - old_tokens + tokens""",
        "new": """            new_used = self._used + tokens""",
    },
    {
        "id": "M10",
        "requirement": "T-04",
        "name": "context_add_ignores_budget",
        "old": """            if new_used > self._budget:""",
        "new": """            if False and new_used > self._budget:""",
    },
    {
        "id": "M11",
        "requirement": "T-05",
        "name": "context_usage_reports_constant_used",
        "old": """                "used": self._used,""",
        "new": """                "used": 0,""",
    },
    {
        "id": "M12",
        "requirement": "T-06",
        "name": "dispatch_drops_tool_arguments",
        "old": """                    value = tool["fn"](**arguments)""",
        "new": """                    value = tool["fn"]()""",
    },
    {
        "id": "M13",
        "requirement": "T-07",
        "name": "unknown_tool_reported_as_tool_failed",
        "old": """        if tool is None:
            raise UnknownTool("unknown tool", name=name, session_id=session_id)""",
        "new": """        if tool is None:
            raise ToolFailed("unknown tool", name=name, session_id=session_id)""",
    },
    {
        "id": "M14",
        "requirement": "T-07",
        "name": "cancelled_argument_not_reserved",
        "old": """        if "cancelled" in arguments:""",
        "new": """        if False and "cancelled" in arguments:""",
    },
    {
        "id": "M15",
        "requirement": "T-08",
        "name": "dispatch_skips_capability_check",
        "old": """        required = tool["required_capability"]
        if required is not None and not self.qatum_check(agent_id, required):""",
        "new": """        required = tool["required_capability"]
        if False and required is not None and not self.qatum_check(agent_id, required):""",
    },
    {
        "id": "M16",
        "requirement": "T-08",
        "name": "dispatch_ignores_session_id",
        "old": """        with self._lock:
            agent_id = self._sessions.get(session_id)
        if agent_id is None:
            raise SessionUnknown("unknown session", session_id=session_id)""",
        "new": """        with self._lock:
            agent_id = next(iter(self._sessions.values()), "anonymous")
        if agent_id is None:
            raise SessionUnknown("unknown session", session_id=session_id)""",
    },
    {
        "id": "M17",
        "requirement": "T-09",
        "name": "qatum_check_reads_rights_from_argument",
        "old": """    def qatum_check(self, agent_id: str, capability: str) -> bool:
        with self._lock:
            return capability in self._grants.get(agent_id, ())""",
        "new": """    def qatum_check(self, agent_id: str, capability: str) -> bool:
        with self._lock:
            return bool(capability)""",
    },
    {
        "id": "M18",
        "requirement": "T-10",
        "name": "qatum_check_accepts_extra_external_arguments",
        "old": """    def qatum_check(self, agent_id: str, capability: str) -> bool:""",
        "new": """    def qatum_check(self, agent_id: str, capability: str, *args: Any, **kwargs: Any) -> bool:""",
    },
    {
        "id": "M19",
        "requirement": "T-11",
        "name": "kernel_returns_anima_id",
        "old": """                "entries": len(self._context),""",
        "new": """                "entries": len(self._context),
                "anima_id": "forged",""",
    },
    {
        "id": "M20",
        "requirement": "T-12",
        "name": "spawn_propagates_oserror",
        "old": """        except (OSError, ValueError, TypeError, IndexError) as exc:""",
        "new": """        except (ValueError, TypeError, IndexError) as exc:""",
    },
    {
        "id": "M21",
        "requirement": "T-13",
        "name": "reap_allows_second_collection",
        "old": """        with self._lock:
            self._procs.pop(pid, None)""",
        "new": """        with self._lock:
            pass""",
    },
    {
        "id": "M22",
        "requirement": "T-14",
        "name": "supervise_returns_int",
        "old": """                return {
                    "pid": current,
                    "restarts": restarts,
                    "history": history,
                }""",
        "new": """                return restarts""",
    },
    {
        "id": "M23",
        "requirement": "T-14",
        "name": "supervise_has_no_pid1_gate",
        "old": """        if os.getpid() != 1:
            raise NotSupervisor(
                "supervise is available only to pid 1", pid=pid
            )""",
        "new": """        if os.getpid() != 1:
            return {"pid": pid, "restarts": 0, "history": []}""",
    },
    {
        "id": "M24",
        "requirement": "T-15",
        "name": "qatum_denied_has_wrong_code",
        "old": """class QatumDenied(KernelError):
    code = 13""",
        "new": """class QatumDenied(KernelError):
    code = 99""",
    },
    {
        "id": "M25",
        "requirement": "T-16",
        "name": "qatum_check_accepts_password",
        "old": """    def qatum_check(self, agent_id: str, capability: str) -> bool:""",
        "new": """    def qatum_check(
        self,
        agent_id: str,
        capability: str,
        password: str | None = None,
    ) -> bool:""",
    },
    {
        "id": "M26",
        "requirement": "T-17",
        "name": "reap_has_no_reap_timeout",
        "old": """        except subprocess.TimeoutExpired:
            duration = time.monotonic() - record["started"]
            raise ReapTimeout(
                "reap timeout", pid=pid, duration=duration
            ) from None""",
        "new": """        except subprocess.TimeoutExpired:
            duration = time.monotonic() - record["started"]
            return {
                "pid": pid,
                "exit_code": None,
                "signal": None,
                "duration": duration,
            }""",
    },
    {
        "id": "M27",
        "requirement": "T-18",
        "name": "close_session_always_true",
        "old": """    def close_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, _MISSING) is not _MISSING""",
        "new": """    def close_session(self, session_id: str) -> bool:
        with self._lock:
            self._sessions.pop(session_id, None)
            return True""",
    },
    {
        "id": "M28",
        "requirement": "T-11",
        "name": "context_add_skips_extra_keys",
        "old": """        if set(entry) != {"key", "tokens"}:
            raise ValueError(
                "context entry must contain exactly key and tokens"
            )""",
        "new": """        if False:
            raise ValueError(
                "context entry must contain exactly key and tokens"
            )""",
    },
    {
        "id": "M29",
        "requirement": "T-18",
        "name": "open_session_ignores_provided_id",
        "old": """    def open_session(
        self, agent_id: str, session_id: str | None = None
    ) -> str:
        with self._lock:
            if session_id is None:
                session_id = uuid.uuid4().hex
                while session_id in self._sessions:
                    session_id = uuid.uuid4().hex
            elif session_id in self._sessions:
                raise ValueError("session_id is already open")
            self._sessions[session_id] = agent_id
        return session_id""",
        "new": """    def open_session(
        self, agent_id: str, session_id: str | None = None
    ) -> str:
        with self._lock:
            session_id = uuid.uuid4().hex
            while session_id in self._sessions:
                session_id = uuid.uuid4().hex
            self._sessions[session_id] = agent_id
        return session_id""",
    },
    {
        "id": "M30",
        "requirement": "T-07",
        "name": "cancellable_grace_floor_is_005",
        "old": """                grace = max(0.1, self._tool_timeout / 2.0)""",
        "new": """                grace = max(0.05, self._tool_timeout / 2.0)""",
    },
    {
        "id": "M31",
        "requirement": "T-03",
        "name": "error_call_not_counted_in_turns",
        "old": """                if reply_type == "error":
                    raise LLMError(
                        "llm_fn reported an error",
                        turns=turns,
                        llm_message=reply.get("message"),
                    )""",
        "new": """                if reply_type == "error":
                    turns -= 1
                    raise LLMError(
                        "llm_fn reported an error",
                        turns=turns,
                        llm_message=reply.get("message"),
                    )""",
    },
    {
        "id": "M32",
        "requirement": "T-18",
        "name": "dispatch_does_not_check_session_ownership",
        "old": """        with self._lock:
            agent_id = self._sessions.get(session_id)
        if agent_id is None:
            raise SessionUnknown("unknown session", session_id=session_id)""",
        "new": """        with self._lock:
            agent_id = self._sessions.get(session_id, "anonymous")
        if agent_id is None:
            raise SessionUnknown("unknown session", session_id=session_id)""",
    },
]


def _run_pytest(root: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "kernel/test_libbu.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
    )


def _apply(source: str, old: str, new: str) -> str:
    occurrences = source.count(old)
    if occurrences != 1:
        raise RuntimeError(f"patch matched {occurrences} times, expected 1")
    return source.replace(old, new)


def main() -> int:
    source = LIBBU.read_text(encoding="utf-8")
    tests = TESTS.read_text(encoding="utf-8")
    init_source = INIT.read_text(encoding="utf-8")

    print("baseline: unmutated suite")
    baseline = _run_pytest(HERE.parent)
    if baseline.returncode != 0:
        print(baseline.stdout)
        print("BASELINE FAILED")
        return 1
    print("baseline: OK\n")

    missed: list[dict[str, str]] = []
    for mutant in MUTANTS:
        label = f"{mutant['id']} {mutant['requirement']} {mutant['name']}"
        try:
            mutated = _apply(source, mutant["old"], mutant["new"])
        except RuntimeError as exc:
            print(f"[PATCH-ERROR] {label}: {exc}")
            missed.append(mutant)
            continue

        try:
            compile(mutated, str(LIBBU), "exec")
        except SyntaxError as exc:
            print(f"[PATCH-ERROR] {label}: syntax error: {exc}")
            missed.append(mutant)
            continue

        with tempfile.TemporaryDirectory(prefix="libbu-mutant-") as tmp:
            root = Path(tmp)
            package = root / "kernel"
            package.mkdir()
            (package / "__init__.py").write_text(
                init_source, encoding="utf-8"
            )
            (package / "libbu.py").write_text(mutated, encoding="utf-8")
            (package / "test_libbu.py").write_text(tests, encoding="utf-8")
            try:
                completed = _run_pytest(root)
            except subprocess.TimeoutExpired:
                print(f"[MISSED]   {label}: pytest timed out")
                missed.append(mutant)
                continue

        caught = completed.returncode != 0
        witnesses = list(
            dict.fromkeys(
                re.findall(
                    r"FAILED\s+\S*test_libbu\.py::(test_\w+)",
                    completed.stdout,
                )
            )
        )
        if witnesses:
            witness = ", ".join(witnesses[:4])
            if len(witnesses) > 4:
                witness += f", ... (+{len(witnesses) - 4})"
        else:
            witness = "pytest rc=" + str(completed.returncode)
        if caught:
            print(f"[CAUGHT]   {label} -> {witness}")
        else:
            print(f"[MISSED]   {label}: suite stayed green")
            missed.append(mutant)

    total = len(MUTANTS)
    caught = total - len(missed)
    print(f"\ncaught {caught}/{total} mutants")
    if missed:
        print("missed:")
        for mutant in missed:
            print(f"  {mutant['id']} {mutant['requirement']} {mutant['name']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
