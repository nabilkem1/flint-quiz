"""Server-side timer tests (TASK-090 / FR-015 / NFR-004 / TEST-027).

The session row is the authoritative timing surface; the client never
participates in the decision. These tests exercise the pure
`evaluate_timers` helper plus the integrated `submit_answer` paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.timers import TimerVerdict, evaluate_timers
from src.agent.tools import ToolDeps, build_tools

from ._tools_fakes import RecordingEmitter, build_fake_search
from .conftest import FakeCosmosRepository, make_session_doc

PRINCIPAL = Principal(entra_oid="user-1")
NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def test_evaluate_timers_ok_within_budget() -> None:
    session = make_session_doc().model_copy(
        update={"started_at": NOW, "question_started_at": NOW}
    )
    out = evaluate_timers(session, now=NOW + timedelta(seconds=10))
    assert out.verdict == TimerVerdict.OK
    assert out.quiz_elapsed_seconds == 10


def test_evaluate_timers_quiz_expired_supersedes_question_expired() -> None:
    session = make_session_doc().model_copy(
        update={"started_at": NOW, "question_started_at": NOW}
    )
    # Both timers exceeded; quiz wins.
    out = evaluate_timers(session, now=NOW + timedelta(seconds=601))
    assert out.verdict == TimerVerdict.QUIZ_EXPIRED


def test_evaluate_timers_question_expired_only() -> None:
    session = make_session_doc().model_copy(
        update={
            "started_at": NOW,
            "question_started_at": NOW - timedelta(seconds=70),
            "time_limit_seconds": 600,
            "per_question_limit_seconds": 60,
        }
    )
    out = evaluate_timers(session, now=NOW)
    assert out.verdict == TimerVerdict.QUESTION_EXPIRED


@pytest.fixture
def deps() -> ToolDeps:
    return ToolDeps(
        repo=FakeCosmosRepository(),
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_submit_after_per_question_expiry_records_unanswered(deps: ToolDeps) -> None:
    session = make_session_doc(n=3).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW - timedelta(seconds=120),
            "question_started_at": NOW - timedelta(seconds=120),
            "time_limit_seconds": 600,
        }
    )
    stored = await deps.repo.create_session(session)
    tools = build_tools(deps)

    result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok is True
    assert result.data["verdict"] == "unanswered"


@pytest.mark.asyncio
async def test_submit_after_per_quiz_expiry_flips_to_scored(deps: ToolDeps) -> None:
    session = make_session_doc(n=3).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW - timedelta(hours=2),
            "question_started_at": NOW - timedelta(hours=2),
            "time_limit_seconds": 600,
        }
    )
    stored = await deps.repo.create_session(session)
    tools = build_tools(deps)

    result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok is True
    assert result.data["done"] is True
    assert result.data["results"]["status"] == "Scored"
