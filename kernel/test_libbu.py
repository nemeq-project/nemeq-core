"""Acceptance tests for the Libbu kernel (SDD v1.3.1, 2026-11-13).

Run from ``/home/daedalus/work/nemeq``::

    python3 -m pytest kernel/test_libbu.py -q
    python3 -m pytest kernel/test_libbu.py --cov=kernel.libbu --cov-fail-under=80 -q
"""

import ast
import copy
import inspect
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from kernel import libbu


# ---------------------------------------------------------------------------
# Helpers (not collected by pytest)
# ---------------------------------------------------------------------------
def _text(text, usage=None):
    reply = {"type": "text", "text": text}
    if usage is not None:
        reply["usage"] = usage
    return reply


def _tool_call(name, arguments=None, usage=None):
    reply = {
        "type": "tool_call",
        "name": name,
        "arguments": {} if arguments is None else arguments,
    }
    if usage is not None:
        reply["usage"] = usage
    return reply


def _responses(*items):
    """Return an llm_fn that serves the given replies in order."""
    queue = list(items)

    def llm(messages, tools):
        if not queue:
            raise AssertionError("llm_fn called after the scripted replies")
        return queue.pop(0)

    return llm


class _SpyKernel(libbu.Kernel):
    """Kernel that records session opening and dispatch binding."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.opened = []
        self.dispatches = []
        self.dispatch_session_open = []

    def open_session(self, agent_id, session_id=None):
        session_id = super().open_session(agent_id, session_id)
        self.opened.append(session_id)
        return session_id

    def dispatch(self, tool_call, session_id):
        self.dispatches.append((tool_call, session_id))
        self.dispatch_session_open.append(session_id in self._sessions)
        return super().dispatch(tool_call, session_id)


def _assert_no_anima(value):
    if isinstance(value, dict):
        assert "anima_id" not in value
        for nested in value.values():
            _assert_no_anima(nested)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            _assert_no_anima(nested)


# ---------------------------------------------------------------------------
# T-00 constructor
# ---------------------------------------------------------------------------
def test_constructor():
    params = inspect.signature(libbu.Kernel).parameters
    assert params["budget"].default == 16000
    assert params["tool_timeout"].default == 30.0
    assert params["llm_fn"].default is None

    for kwargs in (
        {"budget": 0},
        {"budget": -1},
        {"tool_timeout": 0},
        {"tool_timeout": -0.5},
    ):
        with pytest.raises(ValueError):
            libbu.Kernel(**kwargs)

    kernel = libbu.Kernel()
    assert kernel.context_usage()["budget"] == 16000
    with pytest.raises(libbu.LLMError) as excinfo:
        kernel.session_loop([], "agent")
    assert excinfo.value.code == 11


# ---------------------------------------------------------------------------
# T-01 session loop
# ---------------------------------------------------------------------------
def test_session_loop_three_turns():
    def llm(messages, tools):
        llm.calls += 1
        if llm.calls < 3:
            return _tool_call("echo", {"n": llm.calls})
        return _text("done")

    llm.calls = 0
    kernel = _SpyKernel(llm_fn=llm)
    kernel.register_tool("echo", lambda n: {"n": n})

    result = kernel.session_loop(
        [{"role": "user", "content": "hi"}], "agent"
    )

    assert result["text"] == "done"
    assert result["turns"] == 3
    assert llm.calls == 3
    assert len(kernel.opened) == 1
    assert len(kernel.dispatches) == 2
    # The session opened by session_loop is closed in its finally block.
    assert kernel.close_session(kernel.opened[0]) is False


def test_session_binding():
    kernel = _SpyKernel(llm_fn=_responses(_tool_call("ping"), _text("ok")))
    kernel.register_tool("ping", lambda: {"pong": True})

    result = kernel.session_loop([], "agent")

    assert result["text"] == "ok"
    assert len(kernel.opened) == 1
    assert len(kernel.dispatches) == 1
    assert kernel.dispatches[0][1] == kernel.opened[0]
    assert kernel.dispatch_session_open == [True]


def test_session_loop_uses_provided_session_id():
    kernel = _SpyKernel(llm_fn=_responses(_tool_call("ping"), _text("ok")))
    kernel.register_tool("ping", lambda: {"pong": True})

    result = kernel.session_loop([], "agent", session_id="fixed-session")

    assert result["text"] == "ok"
    assert kernel.opened == ["fixed-session"]
    assert kernel.dispatches[0][1] == "fixed-session"
    assert kernel.dispatch_session_open == [True]
    assert kernel.close_session("fixed-session") is False
    with pytest.raises(libbu.SessionUnknown) as excinfo:
        kernel.dispatch(
            {"name": "ping", "arguments": {}}, "fixed-session"
        )
    assert excinfo.value.code == 19


def test_context_ram_untouched():
    kernel = _SpyKernel(
        llm_fn=_responses(_tool_call("noop"), _text("done"))
    )
    kernel.register_tool("noop", lambda: {"ok": True})
    kernel.context_add({"key": "resident", "tokens": 7})
    before_usage = kernel.context_usage()
    before_list = kernel.context_list()

    kernel.session_loop([{"role": "user", "content": "hello"}], "agent")

    assert kernel.context_usage() == before_usage
    assert kernel.context_list() == before_list


def test_tokens_sum():
    kernel = libbu.Kernel(
        llm_fn=_responses(
            _tool_call(
                "noop", usage={"tokens_in": 10, "tokens_out": 5}
            ),
            _text("done", usage={"tokens_in": 7, "tokens_out": 3}),
        )
    )
    kernel.register_tool("noop", lambda: {})

    result = kernel.session_loop([], "agent")

    assert result["turns"] == 2
    assert result["tokens_in"] == 17
    assert result["tokens_out"] == 8


# ---------------------------------------------------------------------------
# T-02 max turns
# ---------------------------------------------------------------------------
def test_session_loop_max_turns():
    kernel = libbu.Kernel(llm_fn=lambda messages, tools: _tool_call("noop"))
    kernel.register_tool("noop", lambda: {})

    with pytest.raises(libbu.MaxTurnsExceeded) as excinfo:
        kernel.session_loop([], "agent", max_turns=2)

    assert excinfo.value.code == 10
    assert excinfo.value.turns == 2
    assert "2" in str(excinfo.value)


# ---------------------------------------------------------------------------
# T-03 LLM responses, tokens, input copy, accumulation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        {"type": "error", "message": "boom"},
        None,
        {"text": "no type"},
        {"type": "mystery"},
    ],
)
def test_session_loop_llm_error(reply):
    kernel = libbu.Kernel(llm_fn=lambda messages, tools: reply)

    with pytest.raises(libbu.LLMError) as excinfo:
        kernel.session_loop(
            [{"role": "user", "content": "x"}], "agent"
        )

    assert excinfo.value.code == 11
    assert excinfo.value.turns == 1
    assert not isinstance(excinfo.value, TypeError)


def test_session_loop_llm_error_after_tool_call():
    kernel = libbu.Kernel(
        llm_fn=_responses(
            _tool_call("noop"),
            {"type": "error", "message": "boom"},
        )
    )
    kernel.register_tool("noop", lambda: {})

    with pytest.raises(libbu.LLMError) as excinfo:
        kernel.session_loop([], "agent")

    assert excinfo.value.code == 11
    assert excinfo.value.turns == 2


def test_tokens_na():
    kernel = libbu.Kernel(
        llm_fn=_responses(
            _tool_call(
                "noop", usage={"tokens_in": 10, "tokens_out": 5}
            ),
            _text("done"),
        )
    )
    kernel.register_tool("noop", lambda: {})

    result = kernel.session_loop([], "agent")

    assert result["tokens_in"] is None
    assert result["tokens_out"] is None

    # A partially specified usage block also makes the corresponding total
    # None rather than a partial sum.
    kernel2 = libbu.Kernel(
        llm_fn=_responses(
            _tool_call("noop", usage={"tokens_in": 10}),
            _text("done", usage={"tokens_out": 3}),
        )
    )
    kernel2.register_tool("noop", lambda: {})
    result2 = kernel2.session_loop([], "agent")
    assert result2["tokens_in"] is None
    assert result2["tokens_out"] is None


def test_llm_input_copy():
    original = [{"role": "user", "content": "hi", "meta": {"x": 1}}]
    seen = []

    def llm(messages, tools):
        seen.append(copy.deepcopy(messages))
        messages[0]["meta"]["x"] = 999
        messages.append({"role": "system", "content": "injected"})
        if len(seen) == 1:
            return _tool_call("noop")
        return _text("done")

    kernel = libbu.Kernel(llm_fn=llm)
    kernel.register_tool("noop", lambda: {"ok": True})

    result = kernel.session_loop(original, "agent")

    assert result["text"] == "done"
    assert original == [
        {"role": "user", "content": "hi", "meta": {"x": 1}}
    ]
    assert len(seen) == 2
    assert seen[1][0]["meta"]["x"] == 1
    assert all(message.get("content") != "injected" for message in seen[1])
    assert seen[1][1]["role"] == "assistant"
    assert seen[1][2]["role"] == "tool"


def test_llm_tools():
    seen = []

    def llm(messages, tools):
        seen.append(tools)
        return _text("ok")

    kernel = libbu.Kernel(llm_fn=llm)
    kernel.register_tool("zeta", lambda: {})
    kernel.register_tool("alpha", lambda: {})
    kernel.register_tool("mu", lambda: {}, required_capability="cap")

    kernel.session_loop([], "agent")

    assert seen == [["alpha", "mu", "zeta"]]


def test_message_accumulation():
    calls = []

    def llm(messages, tools):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1:
            return _tool_call("adder", {"a": 2, "b": 3})
        return _text("done")

    kernel = libbu.Kernel(llm_fn=llm)
    kernel.register_tool("adder", lambda a, b: {"sum": a + b})

    result = kernel.session_loop(
        [{"role": "user", "content": "add"}], "agent"
    )

    assert result["text"] == "done"
    assert calls[0] == [{"role": "user", "content": "add"}]
    assert calls[1][0] == {"role": "user", "content": "add"}
    assert calls[1][1] == {
        "role": "assistant",
        "tool_calls": [
            {"name": "adder", "arguments": {"a": 2, "b": 3}}
        ],
    }
    assert calls[1][2] == {
        "role": "tool",
        "name": "adder",
        "content": {"sum": 5},
    }


def test_usage_only_invalid():
    kernel = libbu.Kernel(llm_fn=lambda messages, tools: {"type": "usage"})

    with pytest.raises(libbu.LLMError) as excinfo:
        kernel.session_loop([], "agent")

    assert excinfo.value.code == 11


# ---------------------------------------------------------------------------
# T-07 reserved argument
# ---------------------------------------------------------------------------
def test_cancelled_reserved():
    kernel = libbu.Kernel()
    session_id = kernel.open_session("agent")
    called = []

    def tool(cancelled):
        called.append(cancelled)
        return {"ok": True}

    kernel.register_tool("tool", tool)

    with pytest.raises(libbu.ToolFailed) as excinfo:
        kernel.dispatch(
            {"name": "tool", "arguments": {"cancelled": True}},
            session_id,
        )

    assert excinfo.value.code == 12
    assert isinstance(excinfo.value.cause, ValueError)
    assert str(excinfo.value.cause) == "reserved argument: cancelled"
    assert called == []


# ---------------------------------------------------------------------------
# T-04 / T-05 context RAM
# ---------------------------------------------------------------------------
def test_context_overflow():
    kernel = libbu.Kernel(budget=10)
    kernel.context_add({"key": "a", "tokens": 6})
    before = kernel.context_usage()

    with pytest.raises(libbu.ContextOverflow) as excinfo:
        kernel.context_add({"key": "b", "tokens": 5})

    assert excinfo.value.code == 16
    assert kernel.context_usage() == before
    assert kernel.context_get("b") is None


def test_context_replace():
    kernel = libbu.Kernel(budget=10)
    kernel.context_add({"key": "a", "tokens": 3})
    kernel.context_add({"key": "b", "tokens": 2})
    assert kernel.context_usage()["used"] == 5

    kernel.context_add({"key": "a", "tokens": 6})

    assert kernel.context_usage()["used"] == 8
    assert kernel.context_usage()["entries"] == 2
    assert kernel.context_get("a") == {"key": "a", "tokens": 6}
    assert [entry["key"] for entry in kernel.context_list()] == ["a", "b"]

    before = kernel.context_usage()
    with pytest.raises(libbu.ContextOverflow):
        kernel.context_add({"key": "a", "tokens": 9})

    assert kernel.context_usage() == before
    assert kernel.context_get("a")["tokens"] == 6


def test_context_usage_deterministic():
    def sequence():
        kernel = libbu.Kernel(budget=100)
        assert kernel.context_usage() == {
            "budget": 100,
            "used": 0,
            "remaining": 100,
            "entries": 0,
        }
        kernel.context_add({"key": "a", "tokens": 5})
        assert kernel.context_usage()["used"] == 5
        kernel.context_add({"key": "b", "tokens": 7})
        assert kernel.context_usage()["used"] == 12
        assert kernel.context_remove("missing") is False
        assert kernel.context_remove("a") is True
        assert kernel.context_usage() == {
            "budget": 100,
            "used": 7,
            "remaining": 93,
            "entries": 1,
        }
        assert kernel.context_get("missing") is None
        return kernel.context_usage()

    assert sequence() == sequence()


# ---------------------------------------------------------------------------
# T-06 dispatch
# ---------------------------------------------------------------------------
def test_dispatch_known_tool():
    received = {}

    def add(x, y):
        received["x"] = x
        received["y"] = y
        return {"sum": x + y}

    kernel = libbu.Kernel()
    kernel.register_tool("add", add)
    session_id = kernel.open_session("agent")

    result = kernel.dispatch(
        {"name": "add", "arguments": {"x": 1, "y": 2}}, session_id
    )

    assert result == {"tool": "add", "result": {"sum": 3}}
    assert received == {"x": 1, "y": 2}

    kernel.register_tool("pairs", lambda: [("sum", 4)])
    result2 = kernel.dispatch(
        {"name": "pairs", "arguments": {}}, session_id
    )
    assert result2["result"] == {"sum": 4}


def test_public_tool():
    kernel = libbu.Kernel()
    kernel.register_tool(
        "public", lambda: {"ok": True}, required_capability=None
    )
    session_id = kernel.open_session("agent")

    assert kernel.qatum_check("agent", "anything") is False
    result = kernel.dispatch(
        {"name": "public", "arguments": {}}, session_id
    )
    assert result["result"] == {"ok": True}


# ---------------------------------------------------------------------------
# T-07 dispatch errors and timeouts
# ---------------------------------------------------------------------------
def test_dispatch_errors():
    kernel = libbu.Kernel(tool_timeout=0.05)
    session_id = kernel.open_session("agent")

    with pytest.raises(libbu.UnknownTool) as excinfo:
        kernel.dispatch({"name": "nope", "arguments": {}}, session_id)
    assert excinfo.value.code == 12
    assert type(excinfo.value).__name__ == "UnknownTool"

    def boom():
        raise ValueError("boom")

    kernel.register_tool("boom", boom)
    with pytest.raises(libbu.ToolFailed) as excinfo:
        kernel.dispatch({"name": "boom", "arguments": {}}, session_id)
    assert excinfo.value.code == 12
    assert isinstance(excinfo.value.cause, ValueError)

    kernel.register_tool("needs_x", lambda x: {"x": x})
    with pytest.raises(libbu.ToolFailed) as excinfo:
        kernel.dispatch(
            {"name": "needs_x", "arguments": {"y": 1}}, session_id
        )
    assert excinfo.value.code == 12
    assert isinstance(excinfo.value.cause, TypeError)

    kernel.register_tool("none", lambda: None)
    with pytest.raises(libbu.ToolFailed) as excinfo:
        kernel.dispatch({"name": "none", "arguments": {}}, session_id)
    assert isinstance(excinfo.value.cause, TypeError)

    release = threading.Event()
    kernel.register_tool("slow", lambda: release.wait(1.0) or {})
    try:
        with pytest.raises(libbu.ToolTimeout) as excinfo:
            kernel.dispatch({"name": "slow", "arguments": {}}, session_id)
        assert excinfo.value.code == 12
        assert type(excinfo.value).__name__ == "ToolTimeout"
    finally:
        release.set()


def test_cancellable_tool(capsys):
    flags = []

    def cooperative(cancelled):
        cancelled.wait(2.0)
        flags.append(cancelled.is_set())
        return {"ok": True}

    kernel = libbu.Kernel(tool_timeout=0.1)
    session_id = kernel.open_session("agent")
    kernel.register_tool("coop", cooperative, cancellable=True)

    with pytest.raises(libbu.ToolTimeout) as excinfo:
        kernel.dispatch({"name": "coop", "arguments": {}}, session_id)

    assert excinfo.value.code == 12
    assert flags == [True]
    assert "ToolLeak" not in capsys.readouterr().err


def test_cancellable_grace_floor(capsys):
    def cooperative_after_delay(cancelled):
        cancelled.wait(2.0)
        time.sleep(0.07)
        return {"ok": True}

    kernel = libbu.Kernel(tool_timeout=0.04)
    session_id = kernel.open_session("agent")
    kernel.register_tool(
        "delayed", cooperative_after_delay, cancellable=True
    )

    with pytest.raises(libbu.ToolTimeout) as excinfo:
        kernel.dispatch({"name": "delayed", "arguments": {}}, session_id)

    assert excinfo.value.code == 12
    # grace = max(0.1, tool_timeout / 2) = 0.1, so this function has time
    # to finish after cancellation and must not produce ToolLeak.
    assert "ToolLeak" not in capsys.readouterr().err


def test_cancellable_ignored(capsys):
    release = threading.Event()

    def stubborn(cancelled):
        release.wait(2.0)
        return {"ok": True}

    kernel = libbu.Kernel(tool_timeout=0.1)
    session_id = kernel.open_session("agent")
    kernel.register_tool("stubborn", stubborn, cancellable=True)

    try:
        with pytest.raises(libbu.ToolTimeout) as excinfo:
            kernel.dispatch(
                {"name": "stubborn", "arguments": {}}, session_id
            )
        assert excinfo.value.code == 12
        stderr = capsys.readouterr().err
        assert "[libbu] warning ToolLeak stubborn" in stderr
    finally:
        release.set()


def test_non_cancellable_leak(capsys):
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(2.0)
        return {"ok": True}

    kernel = libbu.Kernel(tool_timeout=0.1)
    session_id = kernel.open_session("agent")
    kernel.register_tool("slow", slow, cancellable=False)

    try:
        with pytest.raises(libbu.ToolTimeout) as excinfo:
            kernel.dispatch({"name": "slow", "arguments": {}}, session_id)

        assert excinfo.value.code == 12
        assert started.is_set()
        assert not release.is_set()

        stderr = capsys.readouterr().err
        match = re.search(r"\[libbu\] warning ToolLeak slow (\d+)", stderr)
        assert match is not None
        thread_id = int(match.group(1))
        assert any(
            thread.ident == thread_id and thread.is_alive()
            for thread in threading.enumerate()
        )
    finally:
        release.set()


# ---------------------------------------------------------------------------
# T-08 dispatch session and capability checks
# ---------------------------------------------------------------------------
def test_dispatch_denied(capsys):
    called = []
    kernel = libbu.Kernel()
    kernel.register_tool(
        "secret",
        lambda: called.append(1) or {"ok": True},
        required_capability="secret.read",
    )
    session_id = kernel.open_session("bob")

    with pytest.raises(libbu.QatumDenied) as excinfo:
        kernel.dispatch({"name": "secret", "arguments": {}}, session_id)

    assert excinfo.value.code == 13
    assert called == []

    # Fields supplied inside tool_call cannot forge the subject or the grant.
    with pytest.raises(libbu.QatumDenied):
        kernel.dispatch(
            {
                "name": "secret",
                "arguments": {},
                "agent_id": "admin",
                "capability": "secret.read",
            },
            session_id,
        )
    assert called == []

    # The subject is the agent stored in the session; its grant opens the tool.
    kernel.grant("bob", "secret.read")
    result = kernel.dispatch({"name": "secret", "arguments": {}}, session_id)
    assert result["result"] == {"ok": True}
    assert called == [1]

    assert "QatumDenied" in capsys.readouterr().err


def test_dispatch_requires_session():
    signature = inspect.signature(libbu.Kernel.dispatch)
    assert (
        signature.parameters["session_id"].default
        is inspect.Parameter.empty
    )

    kernel = libbu.Kernel()
    kernel.register_tool("public", lambda: {})

    with pytest.raises(TypeError):
        kernel.dispatch({"name": "public", "arguments": {}})
    with pytest.raises(TypeError):
        kernel.dispatch({"name": "public", "arguments": {}}, None)


def test_session_unknown(capsys):
    kernel = libbu.Kernel()
    kernel.register_tool("public", lambda: {})

    with pytest.raises(libbu.SessionUnknown) as excinfo:
        kernel.dispatch({"name": "public", "arguments": {}}, "missing")
    assert excinfo.value.code == 19

    session_id = kernel.open_session("agent")
    assert kernel.close_session(session_id) is True
    with pytest.raises(libbu.SessionUnknown) as excinfo:
        kernel.dispatch({"name": "public", "arguments": {}}, session_id)
    assert excinfo.value.code == 19

    stderr = capsys.readouterr().err
    assert "[libbu] error SessionUnknown 19" in stderr


def test_dispatch_order():
    called = []
    kernel = libbu.Kernel()
    kernel.register_tool(
        "protected",
        lambda: called.append(1) or {"ok": True},
        required_capability="cap",
    )

    # Unknown tool wins over an unknown session.
    with pytest.raises(libbu.UnknownTool):
        kernel.dispatch({"name": "missing", "arguments": {}}, "bad")

    # Known tool: unknown session wins over missing capability.
    with pytest.raises(libbu.SessionUnknown):
        kernel.dispatch({"name": "protected", "arguments": {}}, "bad")

    # Known tool and valid session: capability check denies.
    session_id = kernel.open_session("agent")
    with pytest.raises(libbu.QatumDenied):
        kernel.dispatch({"name": "protected", "arguments": {}}, session_id)

    assert called == []


# ---------------------------------------------------------------------------
# T-09 / T-10 capability check
# ---------------------------------------------------------------------------
def test_capability_check():
    kernel = libbu.Kernel()

    assert kernel.qatum_check("agent", "read") is False
    kernel.grant("agent", "read")
    assert kernel.qatum_check("agent", "read") is True
    kernel.grant("agent", "write")
    assert kernel.qatum_check("agent", "write") is True
    kernel.revoke("agent", "read")
    assert kernel.qatum_check("agent", "read") is False
    assert kernel.qatum_check("other", "read") is False
    kernel.revoke("nobody", "read")


def test_capability_no_external_state():
    kernel = libbu.Kernel()
    kernel.grant("alice", "read")

    # Substituting the subject changes only which registry record is read.
    assert kernel.qatum_check("alice", "read") is True
    assert kernel.qatum_check("bob", "read") is False

    # Extra arguments are rejected by the signature.
    with pytest.raises(TypeError):
        kernel.qatum_check("alice", "read", "extra")
    with pytest.raises(TypeError):
        kernel.qatum_check("alice", "read", grants=["read"])

    # A capability-shaped container passed as an argument is not a grant.
    assert kernel.qatum_check("alice", frozenset({"read"})) is False
    assert kernel.qatum_check("bob", frozenset({"read"})) is False


# ---------------------------------------------------------------------------
# T-11 no anima
# ---------------------------------------------------------------------------
def test_kernel_has_no_anima():
    class_signature = inspect.signature(libbu.Kernel)
    for name, parameter in class_signature.parameters.items():
        assert "anima" not in name.lower()
        assert parameter.kind is not inspect.Parameter.VAR_KEYWORD

    for name, member in inspect.getmembers(
        libbu.Kernel, predicate=inspect.isfunction
    ):
        if name.startswith("_"):
            continue
        signature = inspect.signature(member)
        assert "anima_id" not in signature.parameters, name
        assert not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ), name

    kernel = _SpyKernel(
        llm_fn=_responses(_tool_call("echo"), _text("ok"))
    )
    kernel.register_tool("echo", lambda: {"ok": True})
    session_id = kernel.open_session("agent")
    kernel.grant("agent", "cap")

    values = [
        session_id,
        kernel.context_usage(),
        kernel.context_list(),
        kernel.qatum_check("agent", "cap"),
        kernel.dispatch({"name": "echo", "arguments": {}}, session_id),
        kernel.session_loop(
            [{"role": "user", "content": "hi"}], "agent"
        ),
    ]
    kernel.context_add({"key": "resident", "tokens": 1})
    values.append(kernel.context_get("resident"))
    values.append(kernel.context_list())

    # Extra fields, including anima_id, are refused before any record exists.
    before = kernel.context_usage()
    with pytest.raises(ValueError):
        kernel.context_add(
            {"key": "forged", "tokens": 1, "anima_id": "anima-1"}
        )
    with pytest.raises(ValueError):
        kernel.context_add({"key": "forged", "tokens": 1, "extra": 2})
    with pytest.raises(ValueError):
        kernel.context_add({"key": 1, "tokens": 1})
    with pytest.raises(ValueError):
        kernel.context_add({"key": "forged", "tokens": -1})
    with pytest.raises(ValueError):
        kernel.context_add({"key": "forged", "tokens": 1.5})
    assert kernel.context_get("forged") is None
    assert kernel.context_usage() == before

    resident = kernel.context_get("resident")
    assert resident == {"key": "resident", "tokens": 1}
    assert set(resident) == {"key", "tokens"}
    assert all(
        set(entry) == {"key", "tokens"} for entry in kernel.context_list()
    )

    pid = kernel.spawn([sys.executable, "-c", "pass"])
    values.append(pid)
    values.append(kernel.reap(pid, timeout=5))

    for value in values:
        _assert_no_anima(value)

    with pytest.raises(TypeError):
        kernel.context_add({"key": "x", "tokens": 1}, anima_id="x")
    with pytest.raises(TypeError):
        kernel.grant("agent", "cap", anima_id="x")
    with pytest.raises(TypeError):
        kernel.session_loop([], "agent", anima_id="x")


# ---------------------------------------------------------------------------
# T-18 sessions
# ---------------------------------------------------------------------------
def test_session_lifecycle():
    first = libbu.Kernel()
    second = libbu.Kernel()
    first.register_tool("public", lambda: {"ok": True})
    second.register_tool("public", lambda: {"ok": True})

    assert first.close_session("missing") is False

    first_session = first.open_session("alice")
    second_session = second.open_session("bob")
    assert isinstance(first_session, str)
    assert first_session
    assert isinstance(second_session, str)
    assert second_session

    # Both kernels are active; a session id from another kernel is unknown.
    assert second.close_session(first_session) is False
    with pytest.raises(libbu.SessionUnknown) as excinfo:
        second.dispatch(
            {"name": "public", "arguments": {}}, first_session
        )
    assert excinfo.value.code == 19
    with pytest.raises(libbu.SessionUnknown):
        second.dispatch(
            {"name": "public", "arguments": {}}, "not-opened"
        )
    assert second.dispatch(
        {"name": "public", "arguments": {}}, second_session
    )["result"] == {"ok": True}

    assert first.close_session(first_session) is True
    assert first.close_session(first_session) is False
    with pytest.raises(libbu.SessionUnknown):
        first.dispatch(
            {"name": "public", "arguments": {}}, first_session
        )


def test_session_id_deterministic():
    def scenario():
        kernel = libbu.Kernel()
        first = kernel.open_session("alice", "session-alice")
        second = kernel.open_session("bob", "session-bob")
        return [first, second]

    assert scenario() == ["session-alice", "session-bob"]
    assert scenario() == ["session-alice", "session-bob"]

    kernel = libbu.Kernel()
    held = kernel.open_session("alice", "taken")
    with pytest.raises(ValueError):
        kernel.open_session("bob", "taken")
    assert kernel.close_session("taken") is True
    assert held == "taken"

    defaults = [kernel.open_session(f"agent-{i}") for i in range(10)]
    assert len(set(defaults)) == 10
    assert all(re.fullmatch(r"[0-9a-f]{32}", sid) for sid in defaults)
    assert held not in defaults

    other = libbu.Kernel()
    assert kernel.open_session("same-agent") != other.open_session(
        "same-agent"
    )


# ---------------------------------------------------------------------------
# T-12 spawn
# ---------------------------------------------------------------------------
def test_spawn_and_reap(tmp_path):
    kernel = libbu.Kernel()

    pid = kernel.spawn([sys.executable, "-c", "import sys; sys.exit(3)"])
    result = kernel.reap(pid, timeout=5)
    assert result["pid"] == pid
    assert result["exit_code"] == 3
    assert result["signal"] is None
    assert result["duration"] >= 0

    pid2 = kernel.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    time.sleep(0.05)
    os.kill(pid2, signal.SIGKILL)
    result2 = kernel.reap(pid2, timeout=5)
    assert result2["pid"] == pid2
    assert result2["exit_code"] == -9
    assert result2["signal"] == 9

    # env replaces the parent environment (it is not merged).
    #
    # The child needs LD_LIBRARY_PATH when the interpreter comes from a
    # toolcache build (actions/setup-python), otherwise libpython fails to load
    # and the process dies with 127 before our check runs. Passing it through
    # does not weaken the assertion: PATH must still be absent, which is only
    # true when env replaces the environment instead of merging into it.
    env_code = (
        "import os, sys; "
        "sys.exit(0 if os.environ.get('NEMEQ_ONLY') == '1' "
        "and 'PATH' not in os.environ else 1)"
    )
    minimal_env = {
        "NEMEQ_ONLY": "1",
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", ""),
    }
    pid3 = kernel.spawn(
        [sys.executable, "-c", env_code], env=minimal_env
    )
    assert kernel.reap(pid3, timeout=5)["exit_code"] == 0

    # cwd is honoured; cwd=None inherits the parent working directory.
    cwd_code = (
        "import os, sys; "
        "sys.exit(0 if os.getcwd() == sys.argv[1] else 1)"
    )
    pid4 = kernel.spawn(
        [sys.executable, "-c", cwd_code, str(tmp_path)],
        cwd=str(tmp_path),
    )
    assert kernel.reap(pid4, timeout=5)["exit_code"] == 0

    pid5 = kernel.spawn(
        [
            sys.executable,
            "-c",
            "import os, sys; sys.exit(0 if os.getcwd() == sys.argv[1] else 1)",
            os.getcwd(),
        ]
    )
    assert kernel.reap(pid5, timeout=5)["exit_code"] == 0


def test_spawn_failed(capsys):
    kernel = libbu.Kernel()

    with pytest.raises(libbu.SpawnFailed) as excinfo:
        kernel.spawn(["/definitely/not/a/binary"])

    assert excinfo.value.code == 18
    assert isinstance(excinfo.value.cause, OSError)

    with pytest.raises(libbu.SpawnFailed) as excinfo:
        kernel.spawn([])
    assert excinfo.value.code == 18

    stderr = capsys.readouterr().err
    assert "[libbu] error SpawnFailed 18" in stderr


# ---------------------------------------------------------------------------
# T-13 no zombies
# ---------------------------------------------------------------------------
def test_no_zombies():
    kernel = libbu.Kernel()
    pid = kernel.spawn([sys.executable, "-c", "import sys; sys.exit(5)"])

    assert kernel.reap(pid, timeout=5)["exit_code"] == 5

    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)

    with pytest.raises(libbu.ReapUnknown) as excinfo:
        kernel.reap(pid, timeout=1)
    assert excinfo.value.code == 14


# ---------------------------------------------------------------------------
# T-17 reap timeout
# ---------------------------------------------------------------------------
def test_reap_timeout():
    kernel = libbu.Kernel()
    pid = kernel.spawn(
        [sys.executable, "-c", "import time; time.sleep(0.3)"]
    )

    started = time.monotonic()
    with pytest.raises(libbu.ReapTimeout) as excinfo:
        kernel.reap(pid, timeout=0.05)
    elapsed = time.monotonic() - started

    assert excinfo.value.code == 17
    assert excinfo.value.pid == pid
    assert excinfo.value.duration >= 0
    assert elapsed < 5
    # The process was not killed and remains in the process table.
    assert os.kill(pid, 0) is None

    result = kernel.reap(pid, timeout=5)
    assert result["pid"] == pid
    assert result["exit_code"] == 0
    assert result["signal"] is None


# ---------------------------------------------------------------------------
# T-14 supervise
# ---------------------------------------------------------------------------
def test_supervise_pid1_only():
    if os.getpid() == 1:
        pytest.skip("requires a non-PID-1 test process")

    kernel = libbu.Kernel()
    pid = kernel.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    try:
        with pytest.raises(libbu.NotSupervisor) as excinfo:
            kernel.supervise(pid)
        assert excinfo.value.code == 15
        # NotSupervisor is raised before any impact on the pid.
        assert os.kill(pid, 0) is None
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        kernel.reap(pid, timeout=5)


@pytest.mark.skipif(
    os.getpid() != 1, reason="container-only: requires PID 1"
)
def test_supervise_history(tmp_path):
    kernel = libbu.Kernel()
    marker = tmp_path / "seen"
    script = (
        "import os, sys, time\n"
        "marker = sys.argv[1]\n"
        "if os.path.exists(marker):\n"
        "    sys.exit(0)\n"
        "open(marker, 'w').close()\n"
        "time.sleep(30)\n"
    )
    pid = kernel.spawn([sys.executable, "-c", script, str(marker)])

    def killer():
        time.sleep(0.5)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    thread = threading.Thread(target=killer, daemon=True)
    thread.start()
    try:
        result = kernel.supervise(pid)
    finally:
        thread.join(1.0)

    assert result["restarts"] == 1
    assert result["pid"] != pid
    deaths = [
        event for event in result["history"] if event["event"] == "death"
    ]
    restarts = [
        event for event in result["history"] if event["event"] == "restart"
    ]
    assert deaths
    assert deaths[0]["exit_code"] == -9
    assert deaths[0]["signal"] == 9
    assert restarts
    assert all(
        isinstance(event["timestamp"], float) for event in result["history"]
    )


def test_supervise_returns_dict_shape(monkeypatch):
    kernel = libbu.Kernel()
    pid = kernel.spawn([sys.executable, "-c", "pass"])
    monkeypatch.setattr(libbu.os, "getpid", lambda: 1)

    result = kernel.supervise(pid)

    assert isinstance(result, dict)
    assert set(result) == {"pid", "restarts", "history"}
    assert result["pid"] == pid
    assert result["restarts"] == 0
    assert result["history"] == []


def test_supervise_restart_under_patched_pid1(monkeypatch, tmp_path):
    kernel = libbu.Kernel()
    marker = tmp_path / "seen"
    script = (
        "import os, sys\n"
        "marker = sys.argv[1]\n"
        "if os.path.exists(marker):\n"
        "    sys.exit(0)\n"
        "open(marker, 'w').close()\n"
        "sys.exit(7)\n"
    )
    pid = kernel.spawn([sys.executable, "-c", script, str(marker)])
    monkeypatch.setattr(libbu.os, "getpid", lambda: 1)

    result = kernel.supervise(pid)

    assert result["restarts"] == 1
    assert result["pid"] != pid
    assert [event["event"] for event in result["history"]] == [
        "death",
        "restart",
    ]
    assert result["history"][0]["exit_code"] == 7
    assert result["history"][0]["signal"] is None
    assert all(
        isinstance(event["timestamp"], float) for event in result["history"]
    )


# ---------------------------------------------------------------------------
# T-15 codes / T-16 no authn
# ---------------------------------------------------------------------------
def test_error_codes(capsys):
    expected = {
        10: libbu.MaxTurnsExceeded,
        11: libbu.LLMError,
        12: libbu.ToolError,
        13: libbu.QatumDenied,
        14: libbu.ReapUnknown,
        15: libbu.NotSupervisor,
        16: libbu.ContextOverflow,
        17: libbu.ReapTimeout,
        18: libbu.SpawnFailed,
        19: libbu.SessionUnknown,
    }
    assert set(expected) == set(range(10, 20))
    assert {cls.code for cls in expected.values()} == set(expected)
    assert libbu.KernelError.code == 0
    assert (
        libbu.UnknownTool.code
        == libbu.ToolFailed.code
        == libbu.ToolTimeout.code
        == 12
    )
    assert all(
        issubclass(cls, libbu.KernelError) for cls in expected.values()
    )

    kernel = libbu.Kernel(budget=1)
    kernel.context_add({"key": "a", "tokens": 1})
    with pytest.raises(libbu.ContextOverflow):
        kernel.context_add({"key": "b", "tokens": 1})

    stderr = capsys.readouterr().err
    assert "[libbu] error ContextOverflow 16" in stderr

    # Code 0 is a successful return, not an exception.
    assert kernel.context_usage() == {
        "budget": 1,
        "used": 1,
        "remaining": 0,
        "entries": 1,
    }


def test_no_authn():
    banned = {
        "password",
        "passwd",
        "token",
        "credential",
        "credentials",
        "secret",
        "signature",
    }
    for name, member in inspect.getmembers(
        libbu.Kernel, predicate=inspect.isfunction
    ):
        if name.startswith("_"):
            continue
        for parameter in inspect.signature(member).parameters.values():
            lowered = parameter.name.lower()
            assert not any(word in lowered for word in banned), name

    kernel = libbu.Kernel()
    kernel.register_tool(
        "secret", lambda: {"ok": True}, required_capability="secret"
    )
    alice = kernel.open_session("alice")
    bob = kernel.open_session("bob")
    kernel.grant("alice", "secret")

    assert kernel.qatum_check("alice", "secret") is True
    assert kernel.qatum_check("bob", "secret") is False

    # Credential-shaped fields in tool_call cannot change the subject.
    for extra in (
        {"password": "secret"},
        {"token": "secret"},
        {"credential": "secret"},
    ):
        with pytest.raises(libbu.QatumDenied):
            kernel.dispatch(
                {"name": "secret", "arguments": {}, **extra}, bob
            )

    assert kernel.dispatch(
        {"name": "secret", "arguments": {}}, alice
    )["result"] == {"ok": True}

    kernel.revoke("alice", "secret")
    assert kernel.qatum_check("alice", "secret") is False


# ---------------------------------------------------------------------------
# Wave-1 acceptance thresholds
# ---------------------------------------------------------------------------
def test_stdlib_only():
    source = Path(__file__).with_name("libbu.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                imported.add(node.module.split(".")[0])

    assert imported
    assert imported <= (set(sys.stdlib_module_names) | {"__future__"})
    assert "pytest" not in imported


def test_two_kernels_isolated():
    first = libbu.Kernel()
    second = libbu.Kernel()

    first.grant("alice", "read")
    assert first.qatum_check("alice", "read") is True
    assert second.qatum_check("alice", "read") is False

    first.context_add({"key": "k", "tokens": 3})
    assert second.context_get("k") is None
    assert second.context_usage()["used"] == 0

    first.register_tool("only_first", lambda: {"ok": True})
    session_second = second.open_session("bob")
    with pytest.raises(libbu.UnknownTool):
        second.dispatch(
            {"name": "only_first", "arguments": {}}, session_second
        )

    session_first = first.open_session("alice")
    assert second.close_session(session_first) is False
    assert first.close_session(session_first) is True
