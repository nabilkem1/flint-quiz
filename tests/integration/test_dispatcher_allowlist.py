"""TASK-070 / GOV-010 — dispatcher rejects tools outside the allowlist.

The contract this test pins down:

  * Calling `dispatch("evil_tool", ...)` returns an error envelope with
    `ok=False` and `code="E_UNKNOWN_TOOL"`.
  * Exactly one `agent.unknown_tool` custom event is emitted.
  * That event carries ONLY the rejected name — never the args (GOV-063 /
    audit §5.8). Args may carry weaponised content; we never log them.
  * The corresponding tool body is NEVER invoked. The integration check
    is a counter on the stub bodies: it must be zero after the dispatch.

Also exercises the auth-mismatch defence (GOV-063) and the
plain-vanilla happy path so the test file is self-contained against the
dispatcher's public surface.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest

from src.agent.dispatcher import (
    ALLOWED_TOOLS,
    Dispatcher,
    Principal,
    ToolResult,
)


class _RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, properties):
        # Copy so a mutation by the dispatcher (none expected) does not
        # taint the captured record.
        self.events.append((name, dict(properties)))


class _NeverStore:
    async def get_session(self, session_id, user_id):
        raise AssertionError("session store must not be consulted on the unknown-tool path")

    async def pause_session(self, session):
        raise AssertionError("session store must not be consulted on the unknown-tool path")


def _build_stub_tools() -> tuple[dict[str, Any], dict[str, int]]:
    """Return `(tools_map, call_counter)` — counts every stub invocation."""

    calls: dict[str, int] = defaultdict(int)

    def make_stub(name: str):
        async def _stub(args: dict[str, Any], principal: Principal) -> ToolResult:
            calls[name] += 1
            return ToolResult(ok=True, data={"name": name, "args": args})

        return _stub

    return {name: make_stub(name) for name in ALLOWED_TOOLS}, calls


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_and_event_emitted() -> None:
    tools, calls = _build_stub_tools()
    emitter = _RecordingEmitter()
    dispatcher = Dispatcher(
        tools=tools,
        session_store=_NeverStore(),
        frame_provider=lambda _s: None,  # type: ignore[arg-type]
        emitter=emitter,
    )

    result = await dispatcher.dispatch(
        tool_name="exfiltrate_answer_key",
        args={"session_id": "sess-1", "secret": "ignored"},
        principal=Principal(entra_oid="user-1"),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error["code"] == "E_UNKNOWN_TOOL"

    unknown_events = [e for e in emitter.events if e[0] == "agent.unknown_tool"]
    assert len(unknown_events) == 1
    _, payload = unknown_events[0]
    # The event payload MUST carry only the rejected name — no args, no
    # session_id, no user_id (GOV-063 / audit §5.8). A regression here is
    # a P1 leak.
    assert payload == {"rejected_name": "exfiltrate_answer_key"}

    assert sum(calls.values()) == 0, f"no tool body should have run, got {dict(calls)}"


@pytest.mark.asyncio
async def test_known_tool_passes_through_and_emits_dispatch_span() -> None:
    tools, calls = _build_stub_tools()
    emitter = _RecordingEmitter()
    dispatcher = Dispatcher(
        tools=tools,
        session_store=_NeverStore(),
        frame_provider=lambda _s: None,  # type: ignore[arg-type]
        emitter=emitter,
    )

    result = await dispatcher.dispatch(
        tool_name="list_topics",
        args={"user_id": "user-1"},
        principal=Principal(entra_oid="user-1"),
    )

    assert result.ok is True
    assert calls["list_topics"] == 1
    span_events = [e for e in emitter.events if e[0] == "agent.dispatch.list_topics"]
    assert len(span_events) == 1
    _, props = span_events[0]
    assert props["outcome"] == "ok"
    assert props["cache_hit"] is False
    assert "latency_ms" in props


@pytest.mark.asyncio
async def test_user_id_impersonation_is_rejected() -> None:
    tools, calls = _build_stub_tools()
    emitter = _RecordingEmitter()
    dispatcher = Dispatcher(
        tools=tools,
        session_store=_NeverStore(),
        frame_provider=lambda _s: None,  # type: ignore[arg-type]
        emitter=emitter,
    )

    result = await dispatcher.dispatch(
        tool_name="list_topics",
        args={"user_id": "victim"},
        principal=Principal(entra_oid="attacker"),
    )

    assert result.ok is False
    assert result.error and result.error["code"] == "E_AUTH_MISMATCH"
    assert sum(calls.values()) == 0
    mismatch_events = [e for e in emitter.events if e[0] == "agent.auth_mismatch"]
    assert len(mismatch_events) == 1
    # We deliberately do NOT log the full Entra OID; a prefix is enough.
    _, props = mismatch_events[0]
    assert "victim" not in str(props)
