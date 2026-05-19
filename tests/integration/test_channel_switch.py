"""TASK-068 / FR-009 — voice ↔ text switch on the same session preserves state.

Three properties the channel layer relies on:

  1. The resumed session's durable fields (current_index, language,
     score, answers list) are untouched by a channel switch — the
     dispatcher reads Cosmos every turn (ADR-003).
  2. `ResumeContext.is_channel_switch` is True when the connection
     channel differs from the row's recorded channel, False otherwise.
     The agent factory uses this flag to emit the re-acknowledgement
     line (FR-009 / 004-agent §8) without re-issuing the active
     question.
  3. The persisted `language` is the session's, not the channel's.
     Voice TTS auto-detection cannot override the session language —
     that's the GOV-020 "active language is one value, full stop" rule.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.agent.agent_thread import ThreadRef
from src.agent.resumption import resume_from_session
from src.data.models import Answer, Channel, SessionDoc, SessionStatus, Verdict


def _voice_session() -> SessionDoc:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    return SessionDoc(
        id="sess-1",
        user_id="user-1",
        topic="azure-networking",
        language="es",
        requested_language="es",
        seed="0123456789abcdef",
        shuffled_ids=["q-000-es", "q-001-es", "q-002-es"],
        current_index=1,
        answers=[
            Answer(
                question_id="q-000-es",
                received_raw="la primera",
                received_normalized="A",
                verdict=Verdict.CORRECT,
                score_delta=1.0,
                answered_at=now,
                channel=Channel.VOICE,
                latency_ms=140,
            )
        ],
        score=1.0,
        max_score=3.0,
        status=SessionStatus.ACTIVE,
        started_at=now,
        question_started_at=now,
        time_limit_seconds=600,
        channel=Channel.VOICE,
        thread_id="thread-existing",
        prompt_hash="abc" * 16,
    )


class _Store:
    def __init__(self, session: SessionDoc) -> None:
        self._session = session
        self.attach_calls: list[str] = []

    async def get_session(self, session_id, user_id):
        return deepcopy(self._session)

    async def attach_thread_id(self, session, thread_id):
        self.attach_calls.append(thread_id)
        self._session = session.model_copy(update={"thread_id": thread_id})
        return deepcopy(self._session)


class _ThreadClient:
    async def create(self):  # pragma: no cover - not exercised
        return ThreadRef(id="thread-new")

    async def get(self, thread_id):
        return ThreadRef(id=thread_id)

    async def delete(self, thread_id):  # pragma: no cover - not exercised
        return None


@pytest.mark.asyncio
async def test_voice_to_text_switch_preserves_durable_state() -> None:
    session = _voice_session()
    store = _Store(session)
    threads = _ThreadClient()

    ctx = await resume_from_session(
        session_id=session.id,
        user_id=session.user_id,
        current_channel=Channel.TEXT,
        session_store=store,
        thread_client=threads,
    )

    assert ctx.is_channel_switch is True
    assert ctx.current_channel == Channel.TEXT
    # Durable state is exactly what was on the row — language, index,
    # score, the prior answer. No flip to text-channel anywhere.
    assert ctx.session.language == "es"
    assert ctx.session.channel == Channel.VOICE.value or ctx.session.channel == Channel.VOICE
    assert ctx.session.current_index == 1
    assert ctx.session.score == 1.0
    assert ctx.next_question_id == "q-001-es"
    assert ctx.answered_count == 1
    assert ctx.language == "es"


@pytest.mark.asyncio
async def test_same_channel_resume_is_not_a_switch() -> None:
    session = _voice_session()
    store = _Store(session)
    threads = _ThreadClient()

    ctx = await resume_from_session(
        session_id=session.id,
        user_id=session.user_id,
        current_channel=Channel.VOICE,
        session_store=store,
        thread_client=threads,
    )
    assert ctx.is_channel_switch is False
    assert ctx.current_channel == Channel.VOICE
    assert ctx.next_question_id == "q-001-es"


# ---------------------------------------------------------------------------
# Voice → Text end-to-end (TEST-009 / TASK-106).
#
# Start a quiz in voice, answer the first question; reconnect on text with
# the same `session_id`; the next `submit_answer` lands on the next
# unanswered question, in the persisted language, with the `channel`
# dimension switched from voice → text on the new answer row.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_to_text_continues_next_unanswered_question_in_persisted_language() -> None:
    from datetime import datetime as _dt, timezone as _tz

    from src.agent.dispatcher import Principal
    from src.agent.tools import ToolDeps, build_tools

    from tests.integration._tools_fakes import (
        RecordingEmitter,
        build_fake_search,
    )
    from tests.integration.conftest import FakeCosmosRepository

    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="es", topic="azure-networking"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: _dt(2026, 5, 17, 12, 0, 0, tzinfo=_tz.utc),
    )

    # Seed an Active Spanish voice session.
    voice_session = SessionDoc(
        id="sess-vt",
        user_id="user-1",
        topic="azure-networking",
        language="es",
        requested_language="es",
        seed="0123456789abcdef",
        shuffled_ids=[f"azure-networking-{i:03d}-es" for i in range(3)],
        current_index=0,
        answers=[],
        score=0.0,
        max_score=3.0,
        status=SessionStatus.ACTIVE,
        started_at=_dt(2026, 5, 17, 12, 0, 0, tzinfo=_tz.utc),
        question_started_at=_dt(2026, 5, 17, 12, 0, 0, tzinfo=_tz.utc),
        time_limit_seconds=600,
        channel=Channel.VOICE,
    )
    stored = await repo.create_session(voice_session)
    tools = build_tools(deps)
    principal = Principal(entra_oid="user-1")

    # Q1 answered on voice.
    voice_result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "letra B",
            "channel": "voice",
        },
        principal,
    )
    assert voice_result.ok is True
    assert voice_result.data["verdict"] == "correct"

    # Q2 answered on text (channel switch). Same session_id, same language.
    text_result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[1],
            "raw_answer": "B",
            "channel": "text",
        },
        principal,
    )
    assert text_result.ok is True
    assert text_result.data["verdict"] == "correct"

    # Inspect the persisted row — answers carry the per-submission channel.
    refreshed = await repo.get_session(stored.id, "user-1")
    channels = [
        (a.channel.value if isinstance(a.channel, Channel) else a.channel)
        for a in refreshed.answers
    ]
    assert channels == ["voice", "text"]
    # Language pin preserved across the switch.
    assert refreshed.language == "es"
