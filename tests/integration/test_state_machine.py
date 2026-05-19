"""Session-state-machine tests (TEST-026 / 008-api §4.3).

The repository is the only legitimate state-machine actor: callers may not
mutate ``SessionDoc.status`` directly. Every forbidden transition raises
``SessionStateError``; every allowed transition advances the row and
honors the ``ifMatch`` guard.

Uses the in-memory fake in :mod:`conftest` rather than a live Cosmos
emulator — the state-machine assertion is independent of the etag primitive,
and a fast unit-style test runs on every PR. The companion conditional-write
test (TEST-007) exercises the real Cosmos primitive separately.
"""

from __future__ import annotations

import pytest

from src.common.exceptions import SessionStateError
from src.data.models import SessionStatus

from .conftest import FakeCosmosRepository, make_answer, make_session_doc


@pytest.mark.asyncio
async def test_active_to_active_via_submit_answer() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(n=3))
    updated, persisted = await repo.append_answer_conditional(
        session, make_answer(session.shuffled_ids[0])
    )
    assert persisted is True
    assert updated.status == SessionStatus.ACTIVE
    assert updated.current_index == 1


@pytest.mark.asyncio
async def test_active_to_completed_on_last_answer() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(n=2))
    session, _ = await repo.append_answer_conditional(session, make_answer(session.shuffled_ids[0]))
    session, _ = await repo.append_answer_conditional(session, make_answer(session.shuffled_ids[1]))
    assert session.status == SessionStatus.COMPLETED
    assert session.current_index == 2


@pytest.mark.asyncio
async def test_completed_to_scored_then_terminal() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(n=1))
    session, _ = await repo.append_answer_conditional(session, make_answer(session.shuffled_ids[0]))
    scored = await repo.score_session(session)
    assert scored.status == SessionStatus.SCORED
    assert scored.ttl is not None  # TTL set on terminal transition (TASK-050)


@pytest.mark.asyncio
async def test_pause_then_resume() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc())
    paused = await repo.pause_session(session)
    assert paused.status == SessionStatus.PAUSED
    resumed = await repo.resume_session(paused)
    assert resumed.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_active_to_expired_auto_grades_remaining() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(n=3))
    expired = await repo.expire_session(session)
    assert expired.status == SessionStatus.EXPIRED
    assert len(expired.answers) == 3
    # `use_enum_values=True` means validated answers carry the str value,
    # not the Enum instance — compare against the wire form.
    assert all(str(a.verdict) == "unanswered" for a in expired.answers)
    assert expired.current_index == 3
    assert expired.ttl is not None  # terminal-state TTL applied


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "from_status,target",
    [
        (SessionStatus.SCORED, SessionStatus.ACTIVE),
        (SessionStatus.EXPIRED, SessionStatus.ACTIVE),
        (SessionStatus.COMPLETED, SessionStatus.ACTIVE),
    ],
)
async def test_forbidden_transitions_raise(from_status: SessionStatus, target: SessionStatus) -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(status=from_status))

    with pytest.raises(SessionStateError):
        if target == SessionStatus.ACTIVE:
            await repo.resume_session(session)


@pytest.mark.asyncio
async def test_submit_answer_on_expired_rejected() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(status=SessionStatus.EXPIRED))
    with pytest.raises(SessionStateError):
        await repo.append_answer_conditional(session, make_answer(session.shuffled_ids[0]))


@pytest.mark.asyncio
async def test_submit_answer_on_scored_rejected() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(make_session_doc(status=SessionStatus.SCORED))
    with pytest.raises(SessionStateError):
        await repo.append_answer_conditional(session, make_answer(session.shuffled_ids[0]))
