"""TASK-071 / GOV-001..003 — mid-session prompt mutation halts the session P0.

Two scenarios:

  1. Happy path. `session.prompt_hash` matches `compose(language, frame)`
     → the dispatcher invokes the tool body and returns its result. No
     pause, no mismatch event.

  2. Mutation path. The session row carries a stale or fabricated
     `prompt_hash`. The dispatcher recomputes, finds the mismatch,
     emits `agent.prompt_hash_mismatch`, calls `pause_session`, and
     returns the localised "session paused" error. The tool body is
     NEVER invoked.

We assert the event payload carries truncated hash prefixes (not full
hex) so a log surface mistakenly piping events into a low-tier sink
does not exfiltrate the full identifier.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.agent.dispatcher import ALLOWED_TOOLS, Dispatcher, Principal, ToolResult
from src.agent.prompts.compose import SessionFrame, compose
from src.data.models import Channel, SessionDoc, SessionStatus


def _frame(session: SessionDoc) -> SessionFrame:
    return SessionFrame(
        session_id=session.id,
        user_id=session.user_id,
        topic=session.topic,
        language=session.language,
        channel_at_start="text",
        total=len(session.shuffled_ids),
        time_limit_seconds=session.time_limit_seconds,
        started_at=session.started_at,
    )


def _session(prompt_hash: str) -> SessionDoc:
    return SessionDoc(
        id="sess-1",
        user_id="user-1",
        topic="azure-networking",
        language="en",
        requested_language="en",
        seed="0123456789abcdef",
        shuffled_ids=["q-001-en", "q-002-en"],
        current_index=0,
        answers=[],
        score=0.0,
        max_score=2.0,
        status=SessionStatus.ACTIVE,
        started_at=datetime(2026, 5, 17, 12, 34, 56, tzinfo=timezone.utc),
        question_started_at=datetime(2026, 5, 17, 12, 34, 56, tzinfo=timezone.utc),
        time_limit_seconds=600,
        channel=Channel.TEXT,
        prompt_hash=prompt_hash,
    )


class _Store:
    def __init__(self, session: SessionDoc) -> None:
        self.session = session
        self.pause_calls: int = 0

    async def get_session(self, session_id: str, user_id: str) -> SessionDoc:
        return self.session

    async def pause_session(self, session: SessionDoc) -> SessionDoc:
        self.pause_calls += 1
        # Mirror the real repository: status flips to Paused after the
        # transition guard accepts it. For the test we just record the call.
        self.session = session.model_copy(update={"status": SessionStatus.PAUSED})
        return self.session


class _Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name, props):
        self.events.append((name, dict(props)))


@pytest.fixture
def matching_session_and_dispatcher():
    placeholder = _session(prompt_hash="placeholder")
    frame = _frame(placeholder)
    _, real_hash = compose(language="en", session_frame=frame)
    session = _session(prompt_hash=real_hash)
    store = _Store(session)
    emitter = _Emitter()

    body_calls = {"submit_answer": 0}

    async def submit_body(args, principal):
        body_calls["submit_answer"] += 1
        return ToolResult(ok=True, data={"verdict": "correct"})

    async def passthrough(args, principal):
        return ToolResult(ok=True, data={})

    tools: dict[str, Any] = {name: passthrough for name in ALLOWED_TOOLS}
    tools["submit_answer"] = submit_body

    dispatcher = Dispatcher(
        tools=tools, session_store=store, frame_provider=_frame, emitter=emitter,
    )
    return dispatcher, store, emitter, body_calls, session


@pytest.mark.asyncio
async def test_matching_hash_passes_through(matching_session_and_dispatcher) -> None:
    dispatcher, store, emitter, body_calls, session = matching_session_and_dispatcher

    args = {
        "session_id": session.id,
        "user_id": session.user_id,
        "question_id": "q-001-en",
        "raw_answer": "A",
        "channel": "text",
    }
    result = await dispatcher.dispatch(
        "submit_answer", args, Principal(entra_oid=session.user_id)
    )

    assert result.ok is True
    assert body_calls["submit_answer"] == 1
    assert store.pause_calls == 0
    assert not any(name == "agent.prompt_hash_mismatch" for name, _ in emitter.events)


@pytest.mark.asyncio
async def test_mismatched_hash_halts_session_p0() -> None:
    # Persist a hash that cannot match anything compose() produces.
    session = _session(prompt_hash="deadbeef" * 8)
    store = _Store(session)
    emitter = _Emitter()
    body_calls = {"submit_answer": 0}

    async def submit_body(args, principal):  # pragma: no cover - must not run
        body_calls["submit_answer"] += 1
        raise AssertionError("tool body must not run on a hash mismatch")

    async def passthrough(args, principal):  # pragma: no cover - never called here
        return ToolResult(ok=True, data={})

    tools: dict[str, Any] = {name: passthrough for name in ALLOWED_TOOLS}
    tools["submit_answer"] = submit_body

    dispatcher = Dispatcher(
        tools=tools, session_store=store, frame_provider=_frame, emitter=emitter,
    )

    args = {
        "session_id": session.id,
        "user_id": session.user_id,
        "question_id": "q-001-en",
        "raw_answer": "A",
        "channel": "text",
    }
    result = await dispatcher.dispatch(
        "submit_answer", args, Principal(entra_oid=session.user_id)
    )

    assert result.ok is False
    assert result.error and result.error["code"] == "E_SESSION_PAUSED"
    assert result.error.get("incident") == "PROMPT_HASH_MISMATCH"
    assert body_calls["submit_answer"] == 0
    assert store.pause_calls == 1

    mismatch_events = [e for e in emitter.events if e[0] == "agent.prompt_hash_mismatch"]
    assert len(mismatch_events) == 1
    _, payload = mismatch_events[0]
    # Hash prefixes only — not the full SHA-256.
    assert len(payload["expected_prefix"]) == 12
    assert len(payload["actual_prefix"]) == 12
    assert payload["session_id"] == session.id
    assert payload["tool"] == "submit_answer"
