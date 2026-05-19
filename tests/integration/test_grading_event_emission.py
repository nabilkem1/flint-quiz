"""`grading_event` emission tests (TEST-010 / TASK-141 / NFR-009).

Asserts the load-bearing emission contract:

  * Exactly one event per persisted answer.
  * Required dimensions present (008-api §4.5.1).
  * `expected` and `receivedRaw` ABSENT from the App Insights event.
  * Idempotent no-op `submit_answer` calls DO NOT emit additional
    events (TEST-007 reinforcement).
  * The matching Cosmos `audit` row DOES carry `expected` +
    `receivedRaw` (the two-sink contract from 008-api §4.5).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.observability.events import (
    FORBIDDEN_EVENT_DIMENSIONS,
    AgentEvent,
    EventDimensionError,
    RecordingEmitter,
    emit_grading_event,
)

from ._tools_fakes import build_fake_search
from .conftest import FakeCosmosRepository, make_session_doc

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="user-1")


@pytest.fixture
def deps():
    return ToolDeps(
        repo=FakeCosmosRepository(),
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )


async def _seed_session(deps: ToolDeps, n: int = 3):
    session = make_session_doc(n=n).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(n)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    return await deps.repo.create_session(session)


# ---------------------------------------------------------------------------
# Required dimensions + no-PII property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grading_event_emitted_once_with_all_required_dimensions(deps) -> None:
    stored = await _seed_session(deps)
    tools = build_tools(deps)
    args = {
        "session_id": stored.id,
        "question_id": stored.shuffled_ids[0],
        "raw_answer": "B",
        "channel": "text",
    }
    result = await tools["submit_answer"](args, PRINCIPAL)
    assert result.ok is True

    emitter = deps.emitter
    assert isinstance(emitter, RecordingEmitter)
    events = emitter.find("grading_event")
    assert len(events) == 1, f"expected 1 grading_event, got {len(events)}"

    dims = events[0]
    # All required dimensions present (008-api §4.5.1).
    for required in (
        "session_id",
        "question_id",
        "user_id",
        "language",
        "received",
        "verdict",
        "channel",
        "score_delta",
        "latency_ms",
    ):
        assert required in dims, f"grading_event missing required dim: {required}"

    # Forbidden dimensions absent.
    for forbidden in FORBIDDEN_EVENT_DIMENSIONS:
        assert forbidden not in dims, f"grading_event carries forbidden dim: {forbidden}"


@pytest.mark.asyncio
async def test_idempotent_submit_does_not_double_emit(deps) -> None:
    stored = await _seed_session(deps)
    tools = build_tools(deps)
    args = {
        "session_id": stored.id,
        "question_id": stored.shuffled_ids[0],
        "raw_answer": "B",
        "channel": "text",
    }
    await tools["submit_answer"](args, PRINCIPAL)
    await tools["submit_answer"](args, PRINCIPAL)
    emitter = deps.emitter
    assert isinstance(emitter, RecordingEmitter)
    assert emitter.count("grading_event") == 1


@pytest.mark.asyncio
async def test_audit_row_carries_expected_and_received_raw(deps) -> None:
    """008-api §4.5 — the two-sink contract.

    App Insights `grading_event` has NO `expected`/`receivedRaw`.
    Cosmos `audit` rows DO. This test asserts both sides.
    """

    stored = await _seed_session(deps)
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

    # Audit container — the fake conftest's _FakeContainer keeps every
    # row in `_store`. We read by walking the values.
    audit_rows = [v for v in deps.repo._audit._store.values()]  # type: ignore[attr-defined]
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row["expected"] == ["B"], "audit row must carry the answer key (server-only)"
    assert row["receivedRaw"] == "B", "audit row must carry raw user utterance"

    # And mirror: the App Insights event does NOT carry either.
    emitter = deps.emitter
    grading = emitter.find("grading_event")[0]  # type: ignore[union-attr]
    assert "expected" not in grading
    assert "receivedRaw" not in grading
    assert "received_raw" not in grading
    assert "correct_answer" not in grading


# ---------------------------------------------------------------------------
# Emitter dimension policy
# ---------------------------------------------------------------------------


def test_emit_grading_event_refuses_forbidden_dimension_synthetic() -> None:
    """A naive caller that hand-rolls a dimensions dict MUST be rejected.

    The kwargs-only signature on `emit_grading_event` already prevents
    accidental field addition; this test reinforces the underlying
    `_emit_with_policy` guard with a synthetic invocation through the
    generic `emit_agent_event` path (which IS open to extension and
    therefore must validate).
    """

    from src.observability.events import emit_agent_event

    emitter = RecordingEmitter()
    with pytest.raises(EventDimensionError):
        emit_agent_event(
            emitter,
            AgentEvent.GRADING_EVENT,
            {
                "session_id": "s",
                "question_id": "q",
                "user_id": "u",
                "language": "en",
                "received": "B",
                "verdict": "correct",
                "channel": "text",
                "score_delta": 1.0,
                "latency_ms": 10,
                "timestamp": "2026-05-17T12:00:00Z",
                # SEC-001 violation — must be rejected.
                "correct_answer": ["B"],
            },
        )


def test_emit_grading_event_refuses_missing_dimension() -> None:
    emitter = RecordingEmitter()
    with pytest.raises(EventDimensionError):
        # Missing `latency_ms` + `timestamp`.
        from src.observability.events import emit_agent_event

        emit_agent_event(
            emitter,
            AgentEvent.GRADING_EVENT,
            {
                "session_id": "s",
                "question_id": "q",
                "user_id": "u",
                "language": "en",
                "received": "B",
                "verdict": "correct",
                "channel": "text",
                "score_delta": 1.0,
            },
        )


def test_emit_grading_event_typed_signature_is_kwargs_only() -> None:
    """A positional misuse cannot swap `received` with `received_raw`.

    Calling `emit_grading_event` positionally raises `TypeError` because
    every parameter is keyword-only.
    """

    emitter = RecordingEmitter()
    with pytest.raises(TypeError):
        emit_grading_event(emitter, "s", "q", "u", "en", "B", "correct", "text", 1.0, 10, "t")  # type: ignore[misc]
