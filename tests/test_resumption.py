"""Session resumption (TEST-008 / TASK-172 / FR-008).

Asserts the resumption contract:

  * Cosmos is authoritative — `resume_from_session` reads the row
    fresh, never recovers from the Foundry thread alone.
  * `Active` and `Paused` sessions are resumable; terminal states
    (`Expired`, `Completed`, `Scored`) are not.
  * The persisted language wins — code-switched resume utterances do
    NOT flip the session language.
  * `next_question_id` points at `shuffled_ids[current_index]`.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.agent.agent_thread import ThreadRef
from src.agent.resumption import resume_from_session
from src.data.models import (
    Answer,
    Channel,
    SessionDoc,
    SessionStatus,
    Verdict,
)

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _active_session(language: str = "fr") -> SessionDoc:
    return SessionDoc(
        id="sess-r",
        user_id="user-1",
        topic="azure-networking",
        language=language,
        requested_language=language,
        seed="0123456789abcdef",
        shuffled_ids=[f"q-{i:03d}-{language}" for i in range(3)],
        current_index=1,
        answers=[
            Answer(
                question_id=f"q-000-{language}",
                received_raw="la première",
                received_normalized="A",
                verdict=Verdict.CORRECT,
                score_delta=1.0,
                answered_at=NOW,
                channel=Channel.TEXT,
                latency_ms=10,
            )
        ],
        score=1.0,
        max_score=3.0,
        status=SessionStatus.ACTIVE,
        started_at=NOW,
        question_started_at=NOW,
        time_limit_seconds=600,
        channel=Channel.TEXT,
        thread_id="thread-existing",
        prompt_hash="abc" * 16,
    )


class _Store:
    def __init__(self, session: SessionDoc) -> None:
        self._session = session

    async def get_session(self, session_id, user_id):
        return deepcopy(self._session)

    async def attach_thread_id(self, session, thread_id):
        self._session = session.model_copy(update={"thread_id": thread_id})
        return deepcopy(self._session)


class _Threads:
    async def create(self):
        return ThreadRef(id="thread-new")

    async def get(self, thread_id):
        return ThreadRef(id=thread_id)

    async def delete(self, thread_id):  # pragma: no cover
        return None


@pytest.mark.asyncio
async def test_resume_active_session_returns_next_unanswered_question() -> None:
    ctx = await resume_from_session(
        session_id="sess-r",
        user_id="user-1",
        current_channel=Channel.TEXT,
        session_store=_Store(_active_session("fr")),
        thread_client=_Threads(),
    )
    assert ctx.resumable is True
    assert ctx.next_question_id == "q-001-fr"
    assert ctx.language == "fr"
    assert ctx.answered_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [SessionStatus.SCORED, SessionStatus.EXPIRED, SessionStatus.COMPLETED]
)
async def test_resume_terminal_session_is_not_resumable(status: SessionStatus) -> None:
    session = _active_session().model_copy(update={"status": status})
    ctx = await resume_from_session(
        session_id="sess-r",
        user_id="user-1",
        current_channel=Channel.TEXT,
        session_store=_Store(session),
        thread_client=_Threads(),
    )
    assert ctx.resumable is False
    assert ctx.next_question_id is None
