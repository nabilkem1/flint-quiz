"""Shared helpers for the smoke matrix (TASK-164/165/166).

Each smoke test exercises the **full** in-process agent loop:

  * `start_quiz`        → first question with no `correct_answer`.
  * `submit_answer` ×N  → grading + persisted answers + audit rows.
  * `get_results`       → terminal state + per-question breakdown.

The live-endpoint flavour runs against the deployed Foundry agent in
T2/T5 pipeline tiers; this in-process flavour gates every merge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.data.models import TopicDoc
from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
from tests.integration.conftest import FakeCosmosRepository

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


async def build_smoke_deps(*, language: str, topic: str = "azure-networking"):
    """Construct in-memory ToolDeps for a smoke run."""

    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=5, language=language, topic=topic),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    topic_doc = TopicDoc(
        id=topic,
        topic_id=topic,
        labels={"en": "Networking", "fr": "Réseau", "es": "Redes"},
        counts={"en": 5, "fr": 5, "es": 5},
        default_language="en",
        enabled=True,
        updated_at=NOW,
    )
    payload = topic_doc.model_dump(by_alias=True, exclude_none=True, mode="json")
    await deps.repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]
    return deps


async def run_end_to_end(
    deps: ToolDeps,
    *,
    user_id: str,
    language: str,
    topic: str,
    n: int,
    channel: str,
    answer_resolver,
) -> dict[str, Any]:
    """Run the canonical end-to-end smoke flow and return the final results."""

    principal = Principal(entra_oid=user_id)
    tools = build_tools(deps)

    start = await tools["start_quiz"](
        {
            "user_id": user_id,
            "topic": topic,
            "n": n,
            "language": language,
            "channel": channel,
        },
        principal,
    )
    assert start.ok is True, start.error
    session_id = start.data["session_id"]

    # Read the seeded shuffle from Cosmos so we submit in the order the
    # session expects (NFR-003 permutes the IDs; synthesising
    # `f"{topic}-{i:03d}-..."` would land out-of-order).
    session_row = await deps.repo.get_session(session_id, user_id)
    ordered_question_ids = session_row.shuffled_ids

    for i, question_id in enumerate(ordered_question_ids):
        raw_answer = answer_resolver(i)
        result = await tools["submit_answer"](
            {
                "session_id": session_id,
                "question_id": question_id,
                "raw_answer": raw_answer,
                "channel": channel,
            },
            principal,
        )
        assert result.ok is True, result.error

    final = await tools["get_results"](
        {"session_id": session_id, "user_id": user_id}, principal
    )
    assert final.ok is True
    return final.data
