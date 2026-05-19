"""Negative scenarios (TASK-168 / specs/006-testing-strategy.md §7).

The four cheap-but-valuable negatives the spec calls out:

  1. Spoken answer that doesn't match any option ("the green one") →
     normalizer returns no match; tool re-prompts or grades as
     unanswered.
  2. Coverage fallback when a topic lacks coverage in the requested
     language → agent receives `E_NO_COVERAGE` with `suggested_fallback`.
  3. `submit_answer` against an Expired session → tool refuses.
  4. Concurrent `submit_answer` on same `(session, question)` → one
     answer persisted (covered in `test_idempotency.py`, asserted
     structurally here as a single-line smoke).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.common.exceptions import SessionStateError
from src.data.models import SessionStatus, TopicDoc
from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
from tests.integration.conftest import FakeCosmosRepository, make_session_doc

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="user-1")


async def _deps() -> ToolDeps:
    return ToolDeps(
        repo=FakeCosmosRepository(),
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )


async def _seed_session(deps: ToolDeps, status: SessionStatus = SessionStatus.ACTIVE):
    session = make_session_doc(n=3, status=status).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    return await deps.repo.create_session(session)


# ---------------------------------------------------------------------------
# 1. Spoken no-match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spoken_no_match_does_not_advance_with_correct_verdict() -> None:
    deps = await _deps()
    stored = await _seed_session(deps)
    tools = build_tools(deps)
    result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "the green one",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok
    # The tool grades non-match as `unanswered` (or `incorrect`). Never
    # silently `correct`.
    assert result.data["verdict"] in {"unanswered", "incorrect"}
    assert result.data["score_delta"] == 0.0


# ---------------------------------------------------------------------------
# 2. Coverage fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_fallback_surfaces_suggested_language() -> None:
    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    topic = TopicDoc(
        id="azure-networking",
        topic_id="azure-networking",
        labels={"en": "Networking"},
        counts={"en": 5, "fr": 0},
        default_language="en",
        enabled=True,
        updated_at=NOW,
    )
    payload = topic.model_dump(by_alias=True, exclude_none=True, mode="json")
    await repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]

    tools = build_tools(deps)
    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": "fr",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok is False
    assert result.error["code"] == "E_NO_COVERAGE"
    assert result.error["detail"]["suggested_fallback"] == "en"


# ---------------------------------------------------------------------------
# 3. Expired session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_answer_on_expired_session_rejected() -> None:
    """A session already in Expired state cannot accept new answers."""

    deps = await _deps()
    stored = await _seed_session(deps, status=SessionStatus.EXPIRED)
    tools = build_tools(deps)
    with pytest.raises(SessionStateError):
        await tools["submit_answer"](
            {
                "session_id": stored.id,
                "question_id": stored.shuffled_ids[0],
                "raw_answer": "B",
                "channel": "text",
            },
            PRINCIPAL,
        )


# ---------------------------------------------------------------------------
# 4. Concurrent submit (single-line smoke; full proof in test_idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_submit_answer_persists_one_smoke() -> None:
    deps = await _deps()
    stored = await _seed_session(deps)
    tools = build_tools(deps)
    args = {
        "session_id": stored.id,
        "question_id": stored.shuffled_ids[0],
        "raw_answer": "B",
        "channel": "text",
    }
    await asyncio.gather(*[tools["submit_answer"](args, PRINCIPAL) for _ in range(3)])
    refreshed = await deps.repo.get_session(stored.id, "user-1")
    persisted = [a for a in refreshed.answers if a.question_id == stored.shuffled_ids[0]]
    assert len(persisted) == 1
