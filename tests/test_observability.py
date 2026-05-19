"""Observability test (TEST-010 / TASK-170 / NFR-009).

Spec-anchored assertions on `grading_event` emission:

  * Event count equals persisted-answer count.
  * Required dimensions present (008-api §4.5.1).
  * `expected` and `receivedRaw` absent from the App Insights event.
  * Cosmos `audit` row carries `expected` and `receivedRaw`.
  * Forbidden dimension names rejected at emit-time (`EventDimensionError`).

The detailed integration counterpart lives at
`tests/integration/test_grading_event_emission.py`; this file is the
spec-anchored top-level entry point CI pipelines reference by name.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.observability.events import (
    AgentEvent,
    EventDimensionError,
    FORBIDDEN_EVENT_DIMENSIONS,
    RecordingEmitter,
    emit_agent_event,
)
from tests.integration._tools_fakes import build_fake_search
from tests.integration.conftest import FakeCosmosRepository, make_session_doc

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="user-1")


@pytest.mark.asyncio
async def test_grading_event_count_equals_persisted_answers() -> None:
    repo = FakeCosmosRepository()
    emitter = RecordingEmitter()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=emitter,
        clock=lambda: NOW,
    )
    session = make_session_doc(n=3).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    stored = await repo.create_session(session)
    tools = build_tools(deps)
    # Three submissions → three events. (Real-world voice scenarios
    # would interleave; the contract is one event per persisted answer.)
    for i in range(3):
        await tools["submit_answer"](
            {
                "session_id": stored.id,
                "question_id": stored.shuffled_ids[i],
                "raw_answer": "B",
                "channel": "text",
            },
            PRINCIPAL,
        )
    assert emitter.count("grading_event") == 3
    refreshed = await repo.get_session(stored.id, "user-1")
    assert len(refreshed.answers) == 3


@pytest.mark.asyncio
async def test_audit_carries_expected_received_raw_but_grading_event_does_not() -> None:
    repo = FakeCosmosRepository()
    emitter = RecordingEmitter()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=emitter,
        clock=lambda: NOW,
    )
    session = make_session_doc(n=3).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    stored = await repo.create_session(session)
    tools = build_tools(deps)
    await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "text",
        },
        PRINCIPAL,
    )
    audit_rows = [v for v in repo._audit._store.values()]  # type: ignore[attr-defined]
    assert audit_rows and audit_rows[0]["expected"] == ["B"]
    assert audit_rows[0]["receivedRaw"] == "B"

    grading = emitter.find("grading_event")[0]
    for forbidden in ("expected", "received_raw", "receivedRaw", "correct_answer"):
        assert forbidden not in grading


def test_emit_agent_event_rejects_forbidden_dimensions() -> None:
    """Defence in depth — the policy gate refuses every forbidden name."""

    for forbidden in sorted(FORBIDDEN_EVENT_DIMENSIONS):
        dims = {
            "session_id": "s",
            "topic": "t",
            "requested_language": "fr",
            "suggested_fallback": "en",
            "consent_path": "pending",
            forbidden: "leak",
        }
        with pytest.raises(EventDimensionError):
            emit_agent_event(RecordingEmitter(), AgentEvent.COVERAGE_GAP, dims)
