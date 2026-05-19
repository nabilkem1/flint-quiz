"""Concurrent-`submit_answer` idempotency (TASK-131 / SEC-006 / NFR-002).

The 005-tools test suite already covers the sequential idempotent
case (`tests/integration/test_submit_answer.py`); this test is the
**concurrency** reinforcement called out in TASK-131:

  * Fire N=20 duplicate `submit_answer` calls in parallel.
  * Assert exactly one persisted answer in the session row.
  * Assert exactly one `grading_event` was emitted.
  * Assert exactly one Cosmos `audit` row was written.

The in-memory FakeCosmosRepository implements the `ifMatch` etag
contract from `cosmos_repository.append_answer_conditional` — including
the bounded retry on a 412 race — so this test is the right unit-style
proxy for the real-Cosmos counterpart in 009-testing. The live-Cosmos
version of this test runs against the emulator and is gated by
`COSMOS_EMULATOR_ENDPOINT` (see `tests/integration/conftest.py`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools

from ._tools_fakes import RecordingEmitter, build_fake_search
from .conftest import FakeCosmosRepository, make_session_doc

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="user-1")
N = 20


@pytest.mark.asyncio
async def test_n_concurrent_submit_answer_produces_exactly_one_persisted_answer() -> None:
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
    # Kick off N concurrent calls in a single gather so the event loop
    # is forced to interleave their `replace_item` calls.
    results = await asyncio.gather(*[
        tools["submit_answer"](args, PRINCIPAL) for _ in range(N)
    ])

    # All N callers see `ok=True` with the same verdict.
    assert all(r.ok for r in results)
    verdicts = {r.data["verdict"] for r in results}
    assert verdicts == {"correct"}, f"verdict drift under concurrency: {verdicts}"

    # Exactly one persisted answer.
    refreshed = await repo.get_session(stored.id, "user-1")
    matching = [a for a in refreshed.answers if a.question_id == stored.shuffled_ids[0]]
    assert len(matching) == 1, f"expected exactly one persisted answer, got {len(matching)}"
    assert refreshed.score == 1.0

    # Exactly one `grading_event`.
    assert emitter.count("grading_event") == 1, (
        f"grading_event emitted {emitter.count('grading_event')} times under N={N}"
    )


@pytest.mark.asyncio
async def test_concurrent_submit_answer_writes_exactly_one_audit_row() -> None:
    """TEST-029 — the audit ↔ grading_event symmetry, under concurrency."""

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
    await asyncio.gather(*[tools["submit_answer"](args, PRINCIPAL) for _ in range(N)])

    # The fake _audit container keeps every row written; count matches
    # the grading_event count.
    audit_count = sum(1 for _ in repo._audit._store.values())  # type: ignore[attr-defined]
    assert audit_count == 1, f"expected one audit row under N={N}, got {audit_count}"
