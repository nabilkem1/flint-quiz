"""Voice session length cap tests (TASK-105 / NFR-013).

A simulated 31-minute voice session terminates cleanly. State stays in
Cosmos — the next `submit_answer` for the same `session_id` succeeds
(text or voice).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.voice.session_cap import (
    SessionCap,
    SessionCapConfig,
    SessionCapVerdict,
)

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def test_session_cap_ok_under_budget() -> None:
    cap = SessionCap.start(
        config=SessionCapConfig(max_session_minutes=30),
        clock=lambda: NOW,
    )
    out = cap.tick(now=NOW + timedelta(minutes=29))
    assert out.verdict == SessionCapVerdict.OK


def test_session_cap_close_at_budget() -> None:
    cap = SessionCap.start(
        config=SessionCapConfig(max_session_minutes=30),
        clock=lambda: NOW,
    )
    out = cap.tick(now=NOW + timedelta(minutes=31))
    assert out.verdict == SessionCapVerdict.CLOSE
    assert out.elapsed_seconds >= 30 * 60


@pytest.mark.asyncio
async def test_session_cap_close_preserves_cosmos_state() -> None:
    """A 31-minute cap close does NOT mutate Cosmos.

    The runtime says farewell + closes the WebRTC connection; the
    session row keeps its status (Active / Paused / ...). The next
    `submit_answer` for the same `session_id` succeeds.
    """

    from tests.integration.conftest import FakeCosmosRepository, make_session_doc
    from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
    from src.agent.tools import ToolDeps, build_tools
    from src.agent.dispatcher import Principal

    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        # Pin the per-quiz timer well above 31 min so the cap-close path
        # is what's exercised, not the per-quiz auto-expire.
        clock=lambda: NOW,
    )

    session = make_session_doc(n=3).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
            "time_limit_seconds": 7200,  # 2-hour quiz; cap fires first
        }
    )
    stored = await repo.create_session(session)

    cap = SessionCap.start(
        config=SessionCapConfig(max_session_minutes=30),
        clock=lambda: NOW,
    )
    out = cap.tick(now=NOW + timedelta(minutes=31))
    assert out.verdict == SessionCapVerdict.CLOSE

    # The runtime would close WebRTC here. State in Cosmos is intact.
    # The next submit_answer (any channel) must still grade against it.
    tools = build_tools(deps)
    result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "text",
        },
        Principal(entra_oid=stored.user_id),
    )
    assert result.ok is True
    assert result.data["verdict"] == "correct"
