"""Sweeper integration tests (TEST-027 / TASK-191).

Three transitions, three rows:

* **Stranded release** — Active, current_index=0, started_at > maxStranded.
* **Per-quiz expiry** — Active, started_at > time_limit_seconds.
* **Inactivity pause** — Active, current_index>0, question_started_at >
  pauseThreshold.

A separate scenario asserts the 412 race: a row whose ``_etag`` was rotated
between the feed read and the sweeper's replace call is logged-and-skipped,
not retried last-write-wins.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.data.models import SessionStatus
from src.sweeper.function_app import SweeperConfig, run_sweeper_tick

from .conftest import FakeCosmosRepository, make_answer, make_session_doc


class _StubCfg(SweeperConfig):
    """Bypass env reads; the fake repo doesn't need a real Cosmos endpoint."""

    def __init__(
        self,
        *,
        max_stranded_seconds: int = 300,
        pause_threshold_seconds: int = 600,
    ) -> None:
        self.cosmos_endpoint = "https://fake/"
        self.database = "flint-quiz"
        self.sessions_container = "sessions"
        self.allowed_container = "sessions"
        self.max_stranded_seconds = max_stranded_seconds
        self.pause_threshold_seconds = pause_threshold_seconds


def _aged(delta_seconds: int) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(seconds=delta_seconds)


@pytest.mark.asyncio
async def test_sweeper_releases_stranded_sessions() -> None:
    repo = FakeCosmosRepository()
    session = await repo.create_session(
        make_session_doc(session_id="stranded-1", n=3)
    )
    # Backdate started_at far enough to qualify as stranded.
    session = session.model_copy(update={"started_at": _aged(400)})
    refreshed = await repo._sessions.upsert_item(  # type: ignore[union-attr]
        body=session.model_dump(by_alias=True, exclude_none=True, mode="json")
    )
    assert refreshed["status"] == "Active"

    counters = await run_sweeper_tick(_StubCfg(max_stranded_seconds=300), repo)

    assert counters["stranded_released"] >= 1
    final = await repo.get_session("stranded-1", session.user_id)
    assert final.status == SessionStatus.EXPIRED
    assert all(str(a.verdict) == "unanswered" for a in final.answers)


@pytest.mark.asyncio
async def test_sweeper_expires_per_quiz_timer() -> None:
    repo = FakeCosmosRepository()
    base = make_session_doc(session_id="expired-1", n=3)
    session = base.model_copy(update={"started_at": _aged(700), "time_limit_seconds": 600})
    seeded = await repo.create_session(session)
    # Advance current_index past zero so the row is not classified as stranded.
    seeded, _ = await repo.append_answer_conditional(seeded, make_answer(seeded.shuffled_ids[0]))

    counters = await run_sweeper_tick(_StubCfg(), repo)

    assert counters["expired_swept"] >= 1
    final = await repo.get_session("expired-1", seeded.user_id)
    assert final.status == SessionStatus.EXPIRED


@pytest.mark.asyncio
async def test_sweeper_pauses_inactive_sessions() -> None:
    repo = FakeCosmosRepository()
    base = make_session_doc(session_id="paused-1", n=3)
    seeded = await repo.create_session(base)
    # Drive current_index > 0 via a graded answer so the row isn't stranded.
    seeded, _ = await repo.append_answer_conditional(seeded, make_answer(seeded.shuffled_ids[0]))
    # Backdate question_started_at past the pause threshold but keep started_at recent
    # so we don't trip the per-quiz timer.
    aged = seeded.model_copy(update={"question_started_at": _aged(800)})
    await repo._sessions.upsert_item(  # type: ignore[union-attr]
        body=aged.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

    counters = await run_sweeper_tick(_StubCfg(pause_threshold_seconds=600), repo)

    assert counters["paused_swept"] >= 1
    final = await repo.get_session("paused-1", seeded.user_id)
    assert final.status == SessionStatus.PAUSED


@pytest.mark.asyncio
async def test_sweeper_logs_and_skips_etag_race() -> None:
    """If a real user turn wins the etag race, the sweeper does not retry."""

    repo = FakeCosmosRepository()
    base = make_session_doc(session_id="raced-1", n=3)
    seeded = await repo.create_session(base)
    aged = seeded.model_copy(update={"started_at": _aged(400)})
    await repo._sessions.upsert_item(  # type: ignore[union-attr]
        body=aged.model_dump(by_alias=True, exclude_none=True, mode="json")
    )

    # Race the sweeper: rotate the etag concurrently so the sweeper's
    # replace_item sees a 412. We simulate by mutating the row right
    # before run_sweeper_tick reads it via a small monkey-patch.
    original_query = repo._sessions.query_items  # type: ignore[union-attr]

    async def racing_query(**kw):  # type: ignore[no-untyped-def]
        async for doc in original_query(**kw):
            # Force a parallel mutation between feed read and replace.
            await repo._sessions.upsert_item(  # type: ignore[union-attr]
                body={**doc, "currentIndex": (doc.get("currentIndex") or 0) + 1}
            )
            yield doc

    repo._sessions.query_items = racing_query  # type: ignore[union-attr]

    counters = await run_sweeper_tick(_StubCfg(max_stranded_seconds=300), repo)

    # The sweeper observed the row, attempted to transition, lost the etag
    # race, and did NOT retry. The counter therefore reads zero.
    assert counters["stranded_released"] == 0
    # The row stays Active — the real user's mutation wins.
    final = await repo.get_session("raced-1", base.user_id)
    assert final.status == SessionStatus.ACTIVE
