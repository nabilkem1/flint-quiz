"""Integration tests for `get_results` (TASK-085 / 008-api §1.7)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools

from ._tools_fakes import RecordingEmitter, build_fake_search
from .conftest import FakeCosmosRepository, make_session_doc

PRINCIPAL = Principal(entra_oid="user-1")
NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def deps() -> ToolDeps:
    return ToolDeps(
        repo=FakeCosmosRepository(),
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )


async def _seed_active_session(deps: ToolDeps, *, n: int = 3) -> str:
    session = make_session_doc(n=n).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(n)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    stored = await deps.repo.create_session(session)
    return stored.id


@pytest.mark.asyncio
async def test_get_results_active_returns_not_final(deps: ToolDeps) -> None:
    session_id = await _seed_active_session(deps)
    tools = build_tools(deps)

    result = await tools["get_results"](
        {"session_id": session_id, "user_id": "user-1"}, PRINCIPAL
    )
    assert result.ok is False
    assert result.error["code"] == "E_SESSION_NOT_FINAL"


@pytest.mark.asyncio
async def test_get_results_after_completion_returns_summary(deps: ToolDeps) -> None:
    session_id = await _seed_active_session(deps, n=2)
    tools = build_tools(deps)

    # Walk through to completion.
    for i in range(2):
        await tools["submit_answer"](
            {
                "session_id": session_id,
                "question_id": f"azure-networking-{i:03d}-en",
                "raw_answer": "B",
                "channel": "text",
            },
            PRINCIPAL,
        )
    result = await tools["get_results"](
        {"session_id": session_id, "user_id": "user-1"}, PRINCIPAL
    )
    assert result.ok is True
    data = result.data
    assert data["status"] == "Scored"
    assert data["score"] == 2.0
    assert data["max_score"] == 2.0
    assert data["percentage"] == 100.0
    assert data["pass"] is True
    assert len(data["breakdown"]) == 2

    # SEC-001 — never the key.
    payload = json.dumps(data)
    assert "correct_answer" not in payload
    assert "answer_key" not in payload


@pytest.mark.asyncio
async def test_get_results_pass_threshold_evaluates_correctly(deps: ToolDeps) -> None:
    """Pass threshold defaults to 60% — 1/3 correct → fail; 2/3 → pass."""

    session_id = await _seed_active_session(deps, n=3)
    tools = build_tools(deps)

    # 1 correct + 2 incorrect → 33.3% → fail.
    await tools["submit_answer"](
        {
            "session_id": session_id,
            "question_id": "azure-networking-000-en",
            "raw_answer": "B",
            "channel": "text",
        },
        PRINCIPAL,
    )
    for i in range(1, 3):
        await tools["submit_answer"](
            {
                "session_id": session_id,
                "question_id": f"azure-networking-{i:03d}-en",
                "raw_answer": "A",
                "channel": "text",
            },
            PRINCIPAL,
        )
    result = await tools["get_results"](
        {"session_id": session_id, "user_id": "user-1"}, PRINCIPAL
    )
    assert result.ok is True
    assert result.data["pass"] is False
    assert result.data["percentage"] < 60.0
