"""Session state machine (TEST-026 / TASK-184 / 008-api §4.3).

Top-level spec-anchored re-verification of the state machine. The
integration-tier integration test at
`tests/integration/test_state_machine.py` carries the broader matrix;
this file is the CI-pipeline entry point that lists every forbidden
transition explicitly.
"""

from __future__ import annotations

import pytest

from src.common.exceptions import SessionStateError
from src.data.models import SessionStatus
from tests.integration.conftest import (
    FakeCosmosRepository,
    make_answer,
    make_session_doc,
)


@pytest.mark.asyncio
async def test_state_machine_allowed_transitions_advance() -> None:
    repo = FakeCosmosRepository()
    s = await repo.create_session(make_session_doc(n=1))
    s, _ = await repo.append_answer_conditional(s, make_answer(s.shuffled_ids[0]))
    assert s.status == SessionStatus.COMPLETED
    scored = await repo.score_session(s)
    assert scored.status == SessionStatus.SCORED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "from_status",
    [SessionStatus.SCORED, SessionStatus.EXPIRED, SessionStatus.COMPLETED],
)
async def test_forbidden_terminal_to_active_transition_rejected(from_status) -> None:
    repo = FakeCosmosRepository()
    s = await repo.create_session(make_session_doc(status=from_status))
    with pytest.raises(SessionStateError):
        await repo.resume_session(s)


@pytest.mark.asyncio
async def test_submit_answer_on_terminal_session_is_rejected() -> None:
    repo = FakeCosmosRepository()
    s = await repo.create_session(make_session_doc(status=SessionStatus.EXPIRED))
    with pytest.raises(SessionStateError):
        await repo.append_answer_conditional(s, make_answer(s.shuffled_ids[0]))


@pytest.mark.asyncio
async def test_expire_session_auto_grades_remaining_unanswered() -> None:
    repo = FakeCosmosRepository()
    s = await repo.create_session(make_session_doc(n=3))
    expired = await repo.expire_session(s)
    assert expired.status == SessionStatus.EXPIRED
    assert len(expired.answers) == 3
    assert all(str(a.verdict) == "unanswered" for a in expired.answers)
