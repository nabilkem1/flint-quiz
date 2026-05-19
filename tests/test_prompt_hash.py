"""Prompt-hash stability (TEST-025 / TASK-183 / GOV-003).

The composed system prompt's SHA-256 is pinned at `start_quiz` time.
Every subsequent tool dispatch re-runs `compose(language, frame)`
against the SessionDoc and asserts equality. A mismatch is P0 —
session pauses; on-call paged.

This test:

  * Pins a hash at session-start; runs 5 tool dispatches against the
    pinned hash → all succeed without `agent.prompt_hash_mismatch`.
  * Forces a mismatch by mutating a phrasing block in-memory →
    next dispatch fires `agent.prompt_hash_mismatch` and refuses to
    invoke the tool body.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import (
    ALLOWED_TOOLS,
    Dispatcher,
    Principal,
    ToolResult,
)
from src.agent.prompts.compose import SessionFrame, compose
from src.data.models import Channel

PRINCIPAL = Principal(entra_oid="user-1")
NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _frame(language: str = "en") -> SessionFrame:
    return SessionFrame(
        session_id="sess-1",
        user_id="user-1",
        topic="azure-networking",
        language=language,
        channel_at_start="text",
        total=5,
        time_limit_seconds=600,
        started_at=NOW,
    )


class _Store:
    def __init__(self, session_doc) -> None:
        self.session_doc = session_doc
        self.pause_calls: list[str] = []

    async def get_session(self, session_id: str, user_id: str):
        return self.session_doc

    async def pause_session(self, session):
        self.pause_calls.append(session.id)
        return session


def test_prompt_hash_is_deterministic_for_same_inputs() -> None:
    _, h1 = compose(language="en", session_frame=_frame())
    _, h2 = compose(language="en", session_frame=_frame())
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_prompt_hash_differs_across_languages() -> None:
    _, en = compose(language="en", session_frame=_frame("en"))
    _, fr = compose(language="fr", session_frame=_frame("fr"))
    _, es = compose(language="es", session_frame=_frame("es"))
    assert len({en, fr, es}) == 3


@pytest.mark.asyncio
async def test_dispatcher_passes_when_prompt_hash_matches() -> None:
    """Five sequential dispatches against a session with the correct
    pinned hash all succeed; the dispatcher's prompt-hash check is a
    no-op on the happy path."""

    from src.data.models import SessionDoc, SessionStatus

    composed_prompt, pinned_hash = compose(language="en", session_frame=_frame())
    session = SessionDoc(
        id="sess-1",
        user_id="user-1",
        topic="azure-networking",
        language="en",
        requested_language="en",
        seed="0123456789abcdef",
        shuffled_ids=["q-001-en", "q-002-en", "q-003-en"],
        current_index=0,
        answers=[],
        score=0.0,
        max_score=3.0,
        status=SessionStatus.ACTIVE,
        started_at=NOW,
        question_started_at=NOW,
        time_limit_seconds=600,
        channel=Channel.TEXT,
        prompt_hash=pinned_hash,
    )

    async def ok(args, principal):
        return ToolResult(ok=True, data={"verdict": "correct"})

    tools = {name: ok for name in ALLOWED_TOOLS}
    dispatcher = Dispatcher(
        tools=tools,
        session_store=_Store(session),
        frame_provider=lambda _s: _frame(),
    )
    for _ in range(5):
        result = await dispatcher.dispatch(
            "submit_answer",
            {"session_id": "sess-1", "question_id": "q-001-en", "raw_answer": "B"},
            PRINCIPAL,
        )
        assert result.ok is True


@pytest.mark.asyncio
async def test_dispatcher_pauses_session_on_prompt_hash_mismatch() -> None:
    """A pinned hash that disagrees with the recomputed one is a P0:
    the dispatcher refuses to run the tool body AND pauses the session."""

    from src.data.models import SessionDoc, SessionStatus

    session = SessionDoc(
        id="sess-1",
        user_id="user-1",
        topic="azure-networking",
        language="en",
        requested_language="en",
        seed="0123456789abcdef",
        shuffled_ids=["q-001-en"],
        current_index=0,
        answers=[],
        score=0.0,
        max_score=1.0,
        status=SessionStatus.ACTIVE,
        started_at=NOW,
        question_started_at=NOW,
        time_limit_seconds=600,
        channel=Channel.TEXT,
        # Force a mismatch — a hash that cannot equal the recomputed value.
        prompt_hash="deadbeef" * 8,
    )

    body_invocations: list[str] = []

    async def body(args, principal):
        body_invocations.append("called")
        return ToolResult(ok=True, data={})

    tools = {name: body for name in ALLOWED_TOOLS}
    store = _Store(session)
    events: list[tuple[str, dict]] = []

    class _Emitter:
        def emit(self, name, properties):
            events.append((name, dict(properties)))

    dispatcher = Dispatcher(
        tools=tools,
        session_store=store,
        frame_provider=lambda _s: _frame(),
        emitter=_Emitter(),
    )
    result = await dispatcher.dispatch(
        "submit_answer",
        {"session_id": "sess-1", "question_id": "q-001-en", "raw_answer": "B"},
        PRINCIPAL,
    )
    assert result.ok is False
    assert result.error["code"] == "E_SESSION_PAUSED"
    # Tool body never ran.
    assert body_invocations == []
    # P0 event fired.
    assert any(name == "agent.prompt_hash_mismatch" for name, _ in events), events
    # Session was paused.
    assert store.pause_calls == ["sess-1"]
