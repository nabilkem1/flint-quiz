"""Idempotency under etag concurrency (TEST-007 / NFR-002 / SEC-006).

Two-prong test:

1. **Real Cosmos primitive** — gated on the emulator/cloud endpoint. The
   spec is explicit: the ``ifMatch`` etag behavior must be exercised
   against the real Cosmos contract, not a mock
   (specs/006-testing-strategy §3).

2. **In-memory fake regression** — keeps the test green in CI when the
   emulator is absent, so the idempotent-no-op semantics
   (``persisted=False`` on duplicate submit) don't silently regress.

The contract under test: two concurrent ``append_answer_conditional`` calls
for the same ``(session_id, question_id)`` produce exactly one persisted
answer; the second returns ``persisted=False`` with the same verdict.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from src.data.cosmos_repository import CosmosRepository
from src.data.models import Verdict

from .conftest import (
    FakeCosmosRepository,
    make_answer,
    make_session_doc,
    requires_cosmos,
)


# ---------------------------------------------------------------------------
# Fake-Cosmos regression (always-on)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_submit_returns_idempotent_noop_in_fake() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(n=3))
    qid = session.shuffled_ids[0]

    s1, p1 = await repo.append_answer_conditional(session, make_answer(qid))
    assert p1 is True
    assert s1.current_index == 1
    assert s1.score == 1.0

    # Replay: same (session_id, question_id) pair returns the existing state.
    s2, p2 = await repo.append_answer_conditional(s1, make_answer(qid, verdict=Verdict.INCORRECT))
    assert p2 is False  # the second answer is dropped (008-api §1.6.6 table row 3)
    assert s2.current_index == 1
    assert s2.score == 1.0  # running_score unchanged
    assert str(s2.answers[0].verdict) == "correct"


@pytest.mark.asyncio
async def test_concurrent_submit_persists_exactly_once_in_fake() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(n=3))
    qid = session.shuffled_ids[0]

    # Two callers race with the same session snapshot.
    snap = await repo.get_session(session.id, session.user_id)
    results = await asyncio.gather(
        repo.append_answer_conditional(snap, make_answer(qid)),
        repo.append_answer_conditional(snap, make_answer(qid)),
    )

    # Exactly one of the two writes is `persisted=True`.
    persisted_flags = [persisted for _, persisted in results]
    assert sum(persisted_flags) == 1

    final = await repo.get_session(session.id, session.user_id)
    assert len(final.answers) == 1
    assert final.score == 1.0


# ---------------------------------------------------------------------------
# Real Cosmos emulator path (TEST-007)
# ---------------------------------------------------------------------------


@requires_cosmos
@pytest.mark.asyncio
async def test_duplicate_submit_real_cosmos_ifmatch() -> None:
    endpoint = os.environ.get("COSMOS_TEST_ENDPOINT") or os.environ["COSMOS_EMULATOR_ENDPOINT"]
    repo = CosmosRepository(
        endpoint=endpoint,
        database_name=os.environ.get("COSMOS_TEST_DATABASE", "flint-quiz"),
    )
    try:
        session = await repo.create_session(
            make_session_doc(session_id=f"sess-{uuid.uuid4()}", user_id=f"user-{uuid.uuid4()}")
        )
        qid = session.shuffled_ids[0]

        s1, p1 = await repo.append_answer_conditional(session, make_answer(qid))
        s2, p2 = await repo.append_answer_conditional(s1, make_answer(qid))

        assert p1 is True
        assert p2 is False
        assert s2.score == s1.score
        assert len(s2.answers) == 1
    finally:
        await repo.close()
