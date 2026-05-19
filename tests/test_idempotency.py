"""TEST-007 — `submit_answer` idempotency (TASK-161 / SEC-006 / NFR-002).

Cross-layer assertion under concurrency:

  * **Exactly one** persisted answer for `(session_id, question_id)`
    across N parallel callers.
  * **Exactly one** `grading_event` emission.
  * **Exactly one** Cosmos `audit` row.
  * **Identical verdicts** to every caller.

The contract uses the real Cosmos `ifMatch` etag primitive — the
:class:`FakeCosmosRepository` implements the same etag enforcement
the production SDK exposes, so this test is the right unit-style
proxy. The live-Cosmos counterpart (gated by
``COSMOS_EMULATOR_ENDPOINT``) runs on the T2 merge pipeline.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
from tests.integration.conftest import FakeCosmosRepository, make_session_doc

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="user-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("n_concurrent", [2, 5, 20])
async def test_concurrent_submit_answer_persists_exactly_one(n_concurrent: int) -> None:
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
    args = {
        "session_id": stored.id,
        "question_id": stored.shuffled_ids[0],
        "raw_answer": "B",
        "channel": "text",
    }

    results = await asyncio.gather(
        *[tools["submit_answer"](args, PRINCIPAL) for _ in range(n_concurrent)]
    )
    assert all(r.ok for r in results), [r.error for r in results if not r.ok]
    verdicts = {r.data["verdict"] for r in results}
    assert verdicts == {"correct"}, f"verdict drift under concurrency N={n_concurrent}: {verdicts}"

    refreshed = await repo.get_session(stored.id, "user-1")
    persisted = [a for a in refreshed.answers if a.question_id == stored.shuffled_ids[0]]
    assert len(persisted) == 1
    assert refreshed.score == 1.0
    assert emitter.count("grading_event") == 1
    audit_count = sum(1 for _ in repo._audit._store.values())  # type: ignore[attr-defined]
    assert audit_count == 1


@pytest.mark.asyncio
async def test_retry_after_success_returns_replay_envelope() -> None:
    """A submit retried AFTER the first call has been persisted returns
    the replayed verdict (008-api §1.6.6 / TEST-007). No score
    mutation; no event re-emission."""

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

    first = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert first.ok and first.data["verdict"] == "correct"
    # Sequential retry — score must NOT advance; event must NOT re-emit.
    second = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert second.ok
    assert second.data["verdict"] == first.data["verdict"]
    assert second.data["running_score"] == first.data["running_score"]
    assert emitter.count("grading_event") == 1
