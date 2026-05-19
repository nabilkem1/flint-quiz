"""Tool allowlist + dispatcher mutex (TEST-019 / TASK-177 / GOV-010 / GOV-012).

Two structural assertions on the dispatcher:

  1. `ALLOWED_TOOLS` is exactly the five names from 008-api §1; an
     attempt to dispatch any other name is rejected with
     `E_UNKNOWN_TOOL` and emits `agent.unknown_tool`.

  2. Concurrent `submit_answer` calls for the same
     `(session_id, question_id)` route through the in-process mutex
     so the SECOND caller receives the cached in-flight result
     without re-invoking the tool body.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.agent.dispatcher import (
    ALLOWED_TOOLS,
    Dispatcher,
    Principal,
    ToolResult,
)


PRINCIPAL = Principal(entra_oid="user-1")


class _NullStore:
    async def get_session(self, *_a, **_k):  # pragma: no cover
        raise AssertionError("unknown-tool path must not consult the store")

    async def pause_session(self, *_a, **_k):  # pragma: no cover
        raise AssertionError("unknown-tool path must not pause sessions")


def test_allowlist_is_exactly_the_five_tools() -> None:
    assert ALLOWED_TOOLS == frozenset(
        {"list_topics", "set_language", "start_quiz", "submit_answer", "get_results"}
    )


@pytest.mark.asyncio
async def test_dispatcher_rejects_unknown_tool_name() -> None:
    """A request for an unregistered tool MUST be rejected before any
    tool body runs (GOV-010)."""

    invocations: list[str] = []

    async def passthrough(args, principal):  # pragma: no cover - shouldn't fire
        invocations.append("called")
        return ToolResult(ok=True, data={})

    tools = {name: passthrough for name in ALLOWED_TOOLS}
    events: list[tuple[str, dict[str, Any]]] = []

    class _Emitter:
        def emit(self, name, properties):
            events.append((name, dict(properties)))

    dispatcher = Dispatcher(
        tools=tools,
        session_store=_NullStore(),
        frame_provider=lambda _s: None,  # type: ignore[arg-type]
        emitter=_Emitter(),
    )
    result = await dispatcher.dispatch("evil_tool", {}, PRINCIPAL)
    assert result.ok is False
    assert result.error["code"] == "E_UNKNOWN_TOOL"
    assert not invocations
    assert any(name == "agent.unknown_tool" for name, _ in events), events


def test_dispatcher_mutex_guards_submit_answer() -> None:
    """The mutex BEHAVIOUR (concurrent `submit_answer` → ONE body
    invocation; second caller observes the cached result; per-key
    isolation) is exercised end-to-end with a prompt-hash-matched
    session in ``tests/integration/test_dispatcher_mutex.py``. That
    test is the load-bearing assertion; this file pins the
    surface-level invariant (the constant that names the mutex set).
    """

    from src.agent.dispatcher import _MUTEX_TOOLS  # type: ignore[attr-defined]

    assert "submit_answer" in _MUTEX_TOOLS, (
        "submit_answer MUST be guarded by the per-key dispatcher mutex (GOV-012)"
    )
