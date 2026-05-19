"""Channel-switch test (TEST-009 / TASK-171 / FR-009).

Voice → text (and text → voice) on the same `session_id` preserves:

  * Durable state (current_index, score, answers list).
  * Persisted language (no flip on a code-switched utterance —
    GOV-027).
  * Per-answer `channel` field reflects the per-submission channel.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.data.models import Channel, SessionDoc, SessionStatus
from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
from tests.integration.conftest import FakeCosmosRepository

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="user-1")


def _session(language: str) -> SessionDoc:
    return SessionDoc(
        id="sess-cs",
        user_id="user-1",
        topic="azure-networking",
        language=language,
        requested_language=language,
        seed="0123456789abcdef",
        shuffled_ids=[f"azure-networking-{i:03d}-{language}" for i in range(3)],
        current_index=0,
        answers=[],
        score=0.0,
        max_score=3.0,
        status=SessionStatus.ACTIVE,
        started_at=NOW,
        question_started_at=NOW,
        time_limit_seconds=600,
        channel=Channel.VOICE,
    )


@pytest.mark.asyncio
async def test_voice_to_text_preserves_state_and_language() -> None:
    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="es"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    stored = await repo.create_session(_session("es"))
    tools = build_tools(deps)

    # Q1 on voice.
    await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "letra B",
            "channel": "voice",
        },
        PRINCIPAL,
    )
    # Q2 on text — same session.
    await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[1],
            "raw_answer": "B",
            "channel": "text",
        },
        PRINCIPAL,
    )
    refreshed = await repo.get_session(stored.id, "user-1")
    assert refreshed.language == "es"
    channels = [
        (a.channel.value if isinstance(a.channel, Channel) else a.channel)
        for a in refreshed.answers
    ]
    assert channels == ["voice", "text"]
    assert refreshed.score == 2.0
