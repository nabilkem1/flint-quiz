"""TASK-067 / FR-008 — resume mid-quiz returns the next unanswered question.

Three scenarios:

  1. Resume an `Active` session with two of five questions answered.
     `resume_from_session` returns a ResumeContext with
     `next_question_id` equal to `shuffled_ids[2]`, `answered_count=2`,
     `resumable=True`. The persisted language wins (we resume in
     French, not English).

  2. Resume a `Paused` session — also resumable, with the same
     next-question semantics.

  3. Resume a `Completed` session — `resumable=False`, no next-question
     pointer. The agent factory translates this to the "session is done,
     start a new one" line; the resumption helper just reports the
     state.

We also assert that on a fresh resume (no thread_id persisted), a
Foundry thread is created AND attached to the SessionDoc — durable
state is the authority (ADR-003).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest

from src.agent.agent_thread import ThreadRef
from src.agent.resumption import resume_from_session
from src.data.models import Answer, Channel, SessionDoc, SessionStatus, Verdict


def _session(
    *,
    status: SessionStatus,
    current_index: int = 0,
    answers: list[Answer] | None = None,
    thread_id: str | None = None,
    language: str = "fr",
) -> SessionDoc:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    return SessionDoc(
        id="sess-1",
        user_id="user-1",
        topic="azure-networking",
        language=language,
        requested_language=language,
        seed="0123456789abcdef",
        shuffled_ids=[f"q-{i:03d}-{language}" for i in range(5)],
        current_index=current_index,
        answers=answers or [],
        score=float(len(answers or [])),
        max_score=5.0,
        status=status,
        started_at=now,
        question_started_at=now,
        time_limit_seconds=600,
        channel=Channel.TEXT,
        thread_id=thread_id,
        prompt_hash="abc" * 16,
    )


def _make_answer(question_id: str) -> Answer:
    return Answer(
        question_id=question_id,
        received_raw="A",
        received_normalized="A",
        verdict=Verdict.CORRECT,
        score_delta=1.0,
        answered_at=datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc),
        channel=Channel.TEXT,
        latency_ms=10,
    )


class _Store:
    def __init__(self, session: SessionDoc) -> None:
        self._session = session
        self.attach_calls: list[str] = []

    async def get_session(self, session_id: str, user_id: str) -> SessionDoc:
        assert session_id == self._session.id
        assert user_id == self._session.user_id
        return deepcopy(self._session)

    async def attach_thread_id(self, session: SessionDoc, thread_id: str) -> SessionDoc:
        self.attach_calls.append(thread_id)
        self._session = session.model_copy(update={"thread_id": thread_id})
        return deepcopy(self._session)


class _ThreadClient:
    def __init__(self, *, missing_ids: set[str] | None = None) -> None:
        self._known: dict[str, ThreadRef] = {}
        self._missing = missing_ids or set()
        self.created: list[str] = []
        self.deleted: list[str] = []
        self._counter = 0

    async def create(self) -> ThreadRef:
        self._counter += 1
        ref = ThreadRef(id=f"thread-{self._counter}")
        self._known[ref.id] = ref
        self.created.append(ref.id)
        return ref

    async def get(self, thread_id: str) -> ThreadRef | None:
        if thread_id in self._missing:
            return None
        return self._known.get(thread_id) or ThreadRef(id=thread_id)

    async def delete(self, thread_id: str) -> None:  # pragma: no cover - not exercised here
        self.deleted.append(thread_id)


@pytest.mark.asyncio
async def test_resume_active_returns_next_unanswered_in_session_language() -> None:
    session = _session(
        status=SessionStatus.ACTIVE,
        current_index=2,
        answers=[_make_answer("q-000-fr"), _make_answer("q-001-fr")],
    )
    store = _Store(session)
    threads = _ThreadClient()

    ctx = await resume_from_session(
        session_id=session.id,
        user_id=session.user_id,
        current_channel=Channel.TEXT,
        session_store=store,
        thread_client=threads,
    )

    assert ctx.resumable is True
    assert ctx.next_question_id == "q-002-fr"
    assert ctx.answered_count == 2
    assert ctx.total == 5
    assert ctx.language == "fr"
    # No prior thread → must be created and attached.
    assert threads.created == ["thread-1"]
    assert store.attach_calls == ["thread-1"]
    assert ctx.thread.created_fresh is True


@pytest.mark.asyncio
async def test_resume_paused_is_resumable() -> None:
    session = _session(
        status=SessionStatus.PAUSED,
        current_index=1,
        answers=[_make_answer("q-000-fr")],
        thread_id="thread-existing",
    )
    store = _Store(session)
    threads = _ThreadClient()

    ctx = await resume_from_session(
        session_id=session.id,
        user_id=session.user_id,
        current_channel=Channel.TEXT,
        session_store=store,
        thread_client=threads,
    )

    assert ctx.resumable is True
    assert ctx.next_question_id == "q-001-fr"
    assert threads.created == []  # existing thread looked up, not re-created
    assert store.attach_calls == []
    assert ctx.thread.created_fresh is False


@pytest.mark.asyncio
async def test_resume_completed_returns_non_resumable_context() -> None:
    answers = [_make_answer(f"q-{i:03d}-fr") for i in range(5)]
    session = _session(
        status=SessionStatus.COMPLETED,
        current_index=5,
        answers=answers,
    )
    store = _Store(session)
    threads = _ThreadClient()

    ctx = await resume_from_session(
        session_id=session.id,
        user_id=session.user_id,
        current_channel=Channel.TEXT,
        session_store=store,
        thread_client=threads,
    )

    assert ctx.resumable is False
    assert ctx.next_question_id is None
    assert ctx.answered_count == 5


@pytest.mark.asyncio
async def test_resume_when_foundry_lost_the_thread_creates_a_fresh_one() -> None:
    session = _session(
        status=SessionStatus.ACTIVE,
        current_index=1,
        answers=[_make_answer("q-000-fr")],
        thread_id="thread-evicted",
    )
    store = _Store(session)
    threads = _ThreadClient(missing_ids={"thread-evicted"})

    ctx = await resume_from_session(
        session_id=session.id,
        user_id=session.user_id,
        current_channel=Channel.TEXT,
        session_store=store,
        thread_client=threads,
    )

    assert ctx.resumable is True
    assert ctx.thread.created_fresh is True
    assert threads.created == ["thread-1"]
    assert store.attach_calls == ["thread-1"]
