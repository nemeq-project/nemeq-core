"""Libbu: the deterministic Nemeq kernel (six primitives).

Implements SDD v1.3.1 (2026-11-13).  Standard library only; all mutable state
belongs to a ``Kernel`` instance.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable

__all__ = (
    "Kernel",
    "KernelError",
    "MaxTurnsExceeded",
    "LLMError",
    "ToolError",
    "UnknownTool",
    "ToolFailed",
    "ToolTimeout",
    "QatumDenied",
    "ReapUnknown",
    "NotSupervisor",
    "ContextOverflow",
    "ReapTimeout",
    "SpawnFailed",
    "SessionUnknown",
)

_MISSING = object()


def _emit(kind: str, exc: "KernelError") -> None:
    """Write one machine-readable line to stderr (SDD T-15)."""
    payload: dict[str, Any] = {"message": str(exc)}
    payload.update(getattr(exc, "details", {}))
    try:
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        rendered = "{}"
    print(
        f"[libbu] {kind} {type(exc).__name__} {exc.code} {rendered}",
        file=sys.stderr,
        flush=True,
    )


class KernelError(Exception):
    """Base class for every named kernel refusal (SDD T-15)."""

    code = 0

    def __init__(self, message: str = "", **fields: Any) -> None:
        self.message = message
        self.details: dict[str, Any] = {}
        for key, value in fields.items():
            setattr(self, key, value)
            if key == "cause":
                self.details["cause"] = repr(value)
                self.details["cause_type"] = type(value).__name__
            else:
                self.details[key] = value
        super().__init__(message)
        if self.code:
            _emit("error", self)


class MaxTurnsExceeded(KernelError):
    code = 10


class LLMError(KernelError):
    code = 11


class ToolError(KernelError):
    code = 12


class UnknownTool(ToolError):
    pass


class ToolFailed(ToolError):
    pass


class ToolTimeout(ToolError):
    pass


class QatumDenied(KernelError):
    code = 13


class ReapUnknown(KernelError):
    code = 14


class NotSupervisor(KernelError):
    code = 15


class ContextOverflow(KernelError):
    code = 16


class ReapTimeout(KernelError):
    code = 17


class SpawnFailed(KernelError):
    code = 18


class SessionUnknown(KernelError):
    code = 19


class Kernel:
    """Instance of the Libbu kernel with the six primitives."""

    def __init__(
        self,
        budget: int = 16000,
        tool_timeout: float = 30.0,
        llm_fn: Callable[..., Any] | None = None,
    ) -> None:
        if budget <= 0:
            raise ValueError("budget must be positive")
        if tool_timeout <= 0:
            raise ValueError("tool_timeout must be positive")

        self._budget = budget
        self._tool_timeout = float(tool_timeout)
        self._llm_fn = llm_fn

        self._lock = threading.RLock()
        # Context RAM.
        self._context: dict[str, dict[str, Any]] = {}
        self._used = 0
        # Tool registry, capability registry, sessions.
        self._tools: dict[str, dict[str, Any]] = {}
        self._grants: dict[str, set[str]] = {}
        self._sessions: dict[str, str] = {}
        # Process table.
        self._procs: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Primitive 2: context RAM
    # ------------------------------------------------------------------
    def context_add(self, entry: dict[str, Any]) -> None:
        if not isinstance(entry, dict):
            raise TypeError("context entry must be a dict")
        if set(entry) != {"key", "tokens"}:
            raise ValueError(
                "context entry must contain exactly key and tokens"
            )
        key = entry["key"]
        tokens = entry["tokens"]
        if not isinstance(key, str):
            raise ValueError("context key must be a string")
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            raise ValueError("context tokens must be an integer >= 0")

        with self._lock:
            old = self._context.get(key)
            old_tokens = old["tokens"] if old is not None else 0
            new_used = self._used - old_tokens + tokens
            if new_used > self._budget:
                raise ContextOverflow(
                    "context budget exceeded",
                    key=key,
                    tokens=tokens,
                    used=self._used,
                    budget=self._budget,
                )
            self._context[key] = {"key": key, "tokens": tokens}
            self._used = new_used

    def context_remove(self, key: str) -> bool:
        with self._lock:
            entry = self._context.pop(key, None)
            if entry is None:
                return False
            self._used -= entry["tokens"]
            return True

    def context_get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._context.get(key)
            return copy.deepcopy(entry) if entry is not None else None

    def context_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(entry) for entry in self._context.values()]

    def context_usage(self) -> dict[str, int]:
        with self._lock:
            return {
                "budget": self._budget,
                "used": self._used,
                "remaining": self._budget - self._used,
                "entries": len(self._context),
            }

    # ------------------------------------------------------------------
    # Sessions (instance state, SDD T-18)
    # ------------------------------------------------------------------
    def open_session(
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
        return session_id

    def close_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, _MISSING) is not _MISSING

    # ------------------------------------------------------------------
    # Primitive 4: capability check
    # ------------------------------------------------------------------
    def grant(self, agent_id: str, capability: str) -> None:
        with self._lock:
            self._grants.setdefault(agent_id, set()).add(capability)

    def revoke(self, agent_id: str, capability: str) -> None:
        with self._lock:
            capabilities = self._grants.get(agent_id)
            if capabilities is not None:
                capabilities.discard(capability)

    def qatum_check(self, agent_id: str, capability: str) -> bool:
        with self._lock:
            return capability in self._grants.get(agent_id, ())

    # ------------------------------------------------------------------
    # Primitive 3: dispatch
    # ------------------------------------------------------------------
    def register_tool(
        self,
        name: str,
        fn: Callable[..., Any],
        required_capability: str | None = None,
        cancellable: bool = False,
    ) -> None:
        with self._lock:
            self._tools[name] = {
                "fn": fn,
                "required_capability": required_capability,
                "cancellable": bool(cancellable),
            }

    def dispatch(
        self, tool_call: dict[str, Any], session_id: str
    ) -> dict[str, Any]:
        if session_id is None:
            raise TypeError("session_id is required")

        name = tool_call["name"]
        arguments = tool_call["arguments"]

        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            raise UnknownTool("unknown tool", name=name, session_id=session_id)

        with self._lock:
            agent_id = self._sessions.get(session_id)
        if agent_id is None:
            raise SessionUnknown("unknown session", session_id=session_id)

        required = tool["required_capability"]
        if required is not None and not self.qatum_check(agent_id, required):
            raise QatumDenied(
                "capability denied",
                agent_id=agent_id,
                capability=required,
                tool=name,
            )

        if not isinstance(arguments, dict):
            raise ToolFailed("tool arguments must be a dict", name=name)

        if "cancelled" in arguments:
            cause = ValueError("reserved argument: cancelled")
            raise ToolFailed(
                "reserved argument: cancelled", name=name, cause=cause
            ) from cause

        result = self._invoke_tool(name, tool, arguments)
        return {"tool": name, "result": result}

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return dict(value)

    def _invoke_tool(
        self, name: str, tool: dict[str, Any], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        outcome: dict[str, Any] = {}
        cancellable = tool["cancellable"]
        cancelled = threading.Event() if cancellable else None

        def runner() -> None:
            try:
                if cancelled is not None:
                    value = tool["fn"](**arguments, cancelled=cancelled)
                else:
                    value = tool["fn"](**arguments)
                outcome["result"] = self._as_dict(value)
            except BaseException as exc:  # noqa: BLE001 - reported as ToolFailed
                outcome["error"] = exc

        thread = threading.Thread(
            target=runner, name=f"libbu-tool-{name}", daemon=True
        )
        thread.start()
        thread.join(self._tool_timeout)

        if thread.is_alive():
            if cancelled is not None:
                cancelled.set()
                grace = max(0.1, self._tool_timeout / 2.0)
                thread.join(grace)
                if thread.is_alive():
                    self._warn_leak(name, thread)
            else:
                self._warn_leak(name, thread)
            raise ToolTimeout(
                "tool timed out",
                name=name,
                timeout=self._tool_timeout,
                thread=thread.ident,
            )

        error = outcome.get("error")
        if error is not None:
            raise ToolFailed("tool failed", name=name, cause=error) from error
        if "result" not in outcome:  # pragma: no cover - runner always sets one
            raise ToolFailed(
                "tool produced no result",
                name=name,
                cause=RuntimeError("tool produced no result"),
            )
        return outcome["result"]

    @staticmethod
    def _warn_leak(name: str, thread: threading.Thread) -> None:
        print(
            f"[libbu] warning ToolLeak {name} {thread.ident}",
            file=sys.stderr,
            flush=True,
        )

    # ------------------------------------------------------------------
    # Primitive 1: session loop
    # ------------------------------------------------------------------
    def session_loop(
        self,
        messages: list[dict[str, Any]],
        agent_id: str,
        max_turns: int = 16,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        active_session_id = self.open_session(agent_id, session_id)
        try:
            llm = self._llm_fn
            if llm is None:
                raise LLMError("llm_fn is not configured", turns=0)

            history = copy.deepcopy(messages)
            turns = 0
            tokens_in = 0
            tokens_out = 0
            tokens_in_missing = False
            tokens_out_missing = False

            for _ in range(max_turns):
                tools = sorted(self._tools)
                messages_for_llm = copy.deepcopy(history)
                turns += 1
                try:
                    reply = llm(messages_for_llm, tools)
                except Exception as exc:
                    raise LLMError(
                        "llm_fn raised an exception", turns=turns, cause=exc
                    ) from exc

                if not isinstance(reply, dict):
                    raise LLMError(
                        "llm_fn returned an invalid response",
                        turns=turns,
                        response=repr(reply),
                    )

                usage = reply.get("usage")
                if not isinstance(usage, dict):
                    tokens_in_missing = True
                    tokens_out_missing = True
                else:
                    if isinstance(usage.get("tokens_in"), int):
                        tokens_in += usage["tokens_in"]
                    else:
                        tokens_in_missing = True
                    if isinstance(usage.get("tokens_out"), int):
                        tokens_out += usage["tokens_out"]
                    else:
                        tokens_out_missing = True

                reply_type = reply.get("type")
                if reply_type == "error":
                    raise LLMError(
                        "llm_fn reported an error",
                        turns=turns,
                        llm_message=reply.get("message"),
                    )
                if reply_type == "text":
                    text = reply.get("text")
                    if not isinstance(text, str):
                        raise LLMError(
                            "llm text response has no text", turns=turns
                        )
                    return {
                        "text": text,
                        "turns": turns,
                        "tokens_in": None if tokens_in_missing else tokens_in,
                        "tokens_out": (
                            None if tokens_out_missing else tokens_out
                        ),
                    }
                if reply_type == "tool_call":
                    name = reply.get("name")
                    arguments = reply.get("arguments")
                    if not isinstance(name, str) or not isinstance(arguments, dict):
                        raise LLMError(
                            "llm tool_call response is malformed", turns=turns
                        )
                    history.append(
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "name": name,
                                    "arguments": copy.deepcopy(arguments),
                                }
                            ],
                        }
                    )
                    dispatched = self.dispatch(
                        {"name": name, "arguments": arguments},
                        active_session_id,
                    )
                    history.append(
                        {
                            "role": "tool",
                            "name": name,
                            "content": copy.deepcopy(dispatched["result"]),
                        }
                    )
                    continue

                raise LLMError(
                    "llm_fn returned an unknown response type",
                    turns=turns,
                    response_type=repr(reply_type),
                )

            raise MaxTurnsExceeded(
                f"max turns exceeded: {turns}", turns=turns
            )
        finally:
            self.close_session(active_session_id)

    # ------------------------------------------------------------------
    # Primitive 5: spawn and reap
    # ------------------------------------------------------------------
    def spawn(
        self,
        cmd: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> int:
        try:
            popen = subprocess.Popen(
                cmd, env=env, cwd=cwd, start_new_session=True
            )
        except (OSError, ValueError, TypeError, IndexError) as exc:
            raise SpawnFailed("spawn failed", cmd=cmd, cause=exc) from exc

        record = {
            "popen": popen,
            "cmd": list(cmd),
            "env": None if env is None else dict(env),
            "cwd": cwd,
            "started": time.monotonic(),
        }
        with self._lock:
            self._procs[popen.pid] = record
        return popen.pid

    def reap(self, pid: int, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            record = self._procs.get(pid)
        if record is None:
            raise ReapUnknown("unknown pid", pid=pid)

        popen = record["popen"]
        try:
            returncode = popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - record["started"]
            raise ReapTimeout(
                "reap timeout", pid=pid, duration=duration
            ) from None

        duration = time.monotonic() - record["started"]
        with self._lock:
            self._procs.pop(pid, None)
        signal_number = -returncode if returncode < 0 else None
        return {
            "pid": pid,
            "exit_code": returncode,
            "signal": signal_number,
            "duration": duration,
        }

    # ------------------------------------------------------------------
    # Primitive 6: supervise
    # ------------------------------------------------------------------
    def supervise(self, pid: int) -> dict[str, Any]:
        if os.getpid() != 1:
            raise NotSupervisor(
                "supervise is available only to pid 1", pid=pid
            )

        history: list[dict[str, Any]] = []
        restarts = 0
        current = pid
        while True:
            with self._lock:
                record = self._procs.get(current)
            if record is None:
                raise ReapUnknown("unknown pid", pid=current)

            cmd = list(record["cmd"])
            env = None if record["env"] is None else dict(record["env"])
            cwd = record["cwd"]

            result = self.reap(current)
            if result["exit_code"] == 0:
                return {
                    "pid": current,
                    "restarts": restarts,
                    "history": history,
                }

            history.append(
                {
                    "event": "death",
                    "timestamp": time.time(),
                    "exit_code": result["exit_code"],
                    "signal": result["signal"],
                }
            )
            current = self.spawn(cmd, env=env, cwd=cwd)
            restarts += 1
            history.append(
                {
                    "event": "restart",
                    "timestamp": time.time(),
                    "exit_code": result["exit_code"],
                    "signal": result["signal"],
                }
            )
