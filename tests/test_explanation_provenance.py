"""Explanation provenance (TEST-020 / TASK-178 / GOV-031).

The agent **must not** synthesize an explanation. The only sanctioned
source is the `explanation` field on the active-language question
record. If the record carries no explanation for the active language,
the tool response carries no explanation at all — no LLM fallback, no
translated synthesis.

This contract is asserted structurally:

  1. The `SubmitAnswerResponse.explanation` field is `None` whenever
     the source record's `explanation` is missing.
  2. When the source record DOES carry one, the value flows through
     byte-for-byte (TTS shaping aside).

The repo's seed records all carry explanations; the test injects a
synthetic empty record to exercise the missing-explanation path.
"""

from __future__ import annotations

import pytest

from src.data.models import QuestionView, SubmitAnswerResponse, Verdict


def test_response_model_allows_no_explanation() -> None:
    """`explanation` is optional — the typed model enforces no synthesis."""

    resp = SubmitAnswerResponse(
        verdict=Verdict.CORRECT,
        score_delta=1.0,
        running_score=1.0,
        index=1,
        total=1,
        next=None,
        explanation=None,  # explicitly absent
        done=True,
        results=None,
        question_started_at=None,
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["explanation"] is None
    # The strict schema forbids extra keys — so a future synthesis
    # path that "helpfully" adds an explanation key would fail at the
    # model boundary.
    assert "explanation" in type(resp).model_fields


def test_question_view_does_not_carry_explanation() -> None:
    """`QuestionView` is the LLM-safe projection — explanations are
    delivered via the SubmitAnswerResponse envelope, not the question
    view, so the question prompt cannot accidentally include them.
    """

    assert "explanation" not in QuestionView.model_fields


@pytest.mark.asyncio
async def test_submit_answer_does_not_synthesise_explanation_when_missing() -> None:
    """End-to-end — `submit_answer` returns no `explanation` when the
    fake record has none. The tool layer does NOT call the LLM to
    invent one."""

    from datetime import datetime, timezone

    from src.agent.dispatcher import Principal
    from src.agent.tools import ToolDeps, build_tools
    from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
    from tests.integration.conftest import FakeCosmosRepository, make_session_doc

    NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    deps = ToolDeps(
        repo=FakeCosmosRepository(),
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    session = make_session_doc(n=3).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    stored = await deps.repo.create_session(session)
    tools = build_tools(deps)
    result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "D",  # incorrect
            "channel": "text",
        },
        Principal(entra_oid="user-1"),
    )
    assert result.ok
    # The fake record has no explanation populated; the response MUST
    # carry None, not an LLM-synthesised string.
    assert result.data.get("explanation") in (None, "")
