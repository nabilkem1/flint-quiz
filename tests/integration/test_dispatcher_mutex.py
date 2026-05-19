"""TASK-070 / GOV-012 — concurrent `submit_answer` for same key → one body.

The contract this test pins down (the in-process half of SEC-006; the
cross-process half lives in 003 TASK-047 + tests/integration/test_conditional_write):

  * Two coroutines call `dispatch("submit_answer", ...)` concurrently
    against the same `(session_id, question_id)`. Exactly ONE tool body
    invocation happens.
  * Both coroutines receive the SAME `ToolResult`.
  * The second dispatch records `cache_hit=true` on its
    `agent.dispatch.submit_answer` span; the first records
    `cache_hit=false`.
  * `submit_answer` calls against a DIFFERENT `(session_id, question_id)`
    do not contend — the mutex is keyed.

The dispatcher's prompt-hash verification path is bypassed here by
returning a session whose `prompt_hash` matches a frame_provider that
yields the canonical compose hash.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import pytest

from src.agent.dispatcher import (
    ALLOWED_TOOLS,
    Dispatcher,
    Principal,
    ToolResult,
)
from src.agent.prompts.compose import SessionFrame, compose
from src.data.models import Channel, SessionDoc, SessionStatus


def _make_session(prompt_hash: str) -> SessionDoc:
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


def _frame_for(session: SessionDoc) -> SessionFrame:
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


class _SingleSessionStore:
    def __init__(self, session: SessionDoc) -> None:
        self._session = session
        self.pause_calls: int = 0

    async def get_session(self, session_id: str, user_id: str) -> SessionDoc:
        assert session_id == self._session.id
        assert user_id == self._session.user_id
        return self._session

    async def pause_session(self, session: SessionDoc) -> SessionDoc:
        self.pause_calls += 1
        return session


class _RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, properties):
        self.events.append((name, dict(properties)))


@pytest.fixture
def stub_session_and_hash() -> tuple[SessionDoc, SessionFrame, _SingleSessionStore]:
    session = _make_session(prompt_hash="placeholder")
    frame = _frame_for(session)
    _, real_hash = compose(language="en", session_frame=frame)
    session = _make_session(prompt_hash=real_hash)
    return session, frame, _SingleSessionStore(session)


@pytest.mark.asyncio
async def test_two_concurrent_submit_answers_share_one_invocation(
    stub_session_and_hash: tuple[SessionDoc, SessionFrame, _SingleSessionStore],
) -> None:
    session, frame, store = stub_session_and_hash

    invocations: dict[str, int] = defaultdict(int)
    proceed = asyncio.Event()

    async def slow_submit(args: dict[str, Any], principal: Principal) -> ToolResult:
        invocations["submit_answer"] += 1
        await proceed.wait()
        return ToolResult(
            ok=True,
            data={
                "verdict": "correct",
                "score_delta": 1.0,
                "running_score": 1.0,
                "index": 1,
                "total": 2,
            },
        )

    async def passthrough(args: dict[str, Any], principal: Principal) -> ToolResult:
        return ToolResult(ok=True, data={})

    tools: dict[str, Any] = {name: passthrough for name in ALLOWED_TOOLS}
    tools["submit_answer"] = slow_submit

    emitter = _RecordingEmitter()
    dispatcher = Dispatcher(
        tools=tools,
        session_store=store,
        frame_provider=lambda _s: frame,
        emitter=emitter,
    )

    args = {
        "session_id": session.id,
        "user_id": session.user_id,
        "question_id": "q-001-en",
        "raw_answer": "A",
        "channel": "text",
    }
    principal = Principal(entra_oid=session.user_id)

    # Kick off the owner, give it a moment to claim the slot, then start
    # the second caller. Both await the same future.
    owner = asyncio.create_task(dispatcher.dispatch("submit_answer", args, principal))
    await asyncio.sleep(0)  # yield so owner enters the mutex
    waiter = asyncio.create_task(dispatcher.dispatch("submit_answer", args, principal))
    await asyncio.sleep(0)

    proceed.set()
    result_owner, result_waiter = await asyncio.gather(owner, waiter)

    assert invocations["submit_answer"] == 1, (
        f"expected exactly one tool-body invocation, got {dict(invocations)}"
    )
    assert result_owner.ok is True
    assert result_waiter.ok is True
    assert result_owner.data == result_waiter.data

    spans = [e for e in emitter.events if e[0] == "agent.dispatch.submit_answer"]
    assert len(spans) == 2
    cache_hits = [props["cache_hit"] for _, props in spans]
    assert sorted(cache_hits) == [False, True], (
        f"one owner + one cached follower expected; got cache_hits={cache_hits}"
    )


@pytest.mark.asyncio
async def test_distinct_keys_do_not_contend(
    stub_session_and_hash: tuple[SessionDoc, SessionFrame, _SingleSessionStore],
) -> None:
    session, frame, store = stub_session_and_hash

    invocations: dict[tuple[str, str], int] = defaultdict(int)

    async def submit(args: dict[str, Any], principal: Principal) -> ToolResult:
        invocations[(args["session_id"], args["question_id"])] += 1
        return ToolResult(ok=True, data={"verdict": "correct"})

    async def passthrough(args, principal):
        return ToolResult(ok=True, data={})

    tools: dict[str, Any] = {name: passthrough for name in ALLOWED_TOOLS}
    tools["submit_answer"] = submit

    dispatcher = Dispatcher(
        tools=tools,
        session_store=store,
        frame_provider=lambda _s: frame,
    )
    principal = Principal(entra_oid=session.user_id)

    args_a = dict(
        session_id=session.id, user_id=session.user_id,
        question_id="q-001-en", raw_answer="A", channel="text",
    )
    args_b = dict(
        session_id=session.id, user_id=session.user_id,
        question_id="q-002-en", raw_answer="B", channel="text",
    )
    await asyncio.gather(
        dispatcher.dispatch("submit_answer", args_a, principal),
        dispatcher.dispatch("submit_answer", args_b, principal),
    )

    assert invocations[(session.id, "q-001-en")] == 1
    assert invocations[(session.id, "q-002-en")] == 1
