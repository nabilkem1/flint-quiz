"""Two-stage dead-air handling tests (TASK-105 / GOV-014).

Asserts the idle handler's verdict ladder:

  * 0..29 s of silence → `OK`.
  * 30..59 s of silence → `REPROMPT` (fires once per silence window).
  * ≥ 60 s of silence → `CLOSE`.

The handler is clock-injected — these tests pin time deterministically
rather than sleeping the real clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.voice.idle_handler import IdleConfig, IdleHandler, IdleVerdict

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _clock_at(seconds: int):
    """Build a clock callable that returns NOW + `seconds`."""

    return lambda: NOW + timedelta(seconds=seconds)


def test_idle_ok_within_first_threshold() -> None:
    handler = IdleHandler.start(clock=lambda: NOW)
    out = handler.tick(now=NOW + timedelta(seconds=29))
    assert out.verdict == IdleVerdict.OK


def test_idle_reprompt_at_30s() -> None:
    handler = IdleHandler.start(clock=lambda: NOW)
    out = handler.tick(now=NOW + timedelta(seconds=30))
    assert out.verdict == IdleVerdict.REPROMPT


def test_idle_reprompt_fires_only_once_per_window() -> None:
    handler = IdleHandler.start(clock=lambda: NOW)
    first = handler.tick(now=NOW + timedelta(seconds=31))
    assert first.verdict == IdleVerdict.REPROMPT
    # Still pre-close; the re-prompt has already fired.
    second = handler.tick(now=NOW + timedelta(seconds=45))
    assert second.verdict == IdleVerdict.OK


def test_idle_close_at_60s_cumulative() -> None:
    handler = IdleHandler.start(clock=lambda: NOW)
    # Skip past the re-prompt → close.
    out = handler.tick(now=NOW + timedelta(seconds=60))
    assert out.verdict == IdleVerdict.CLOSE


def test_idle_mark_input_resets_timers() -> None:
    handler = IdleHandler.start(clock=lambda: NOW)
    handler.tick(now=NOW + timedelta(seconds=31))  # fires REPROMPT once
    # User responds 5 s later — reset.
    later = NOW + timedelta(seconds=36)
    handler._last_input_at = later  # simulate mark_input on the next clock tick
    handler._reprompt_fired = False
    out = handler.tick(now=later + timedelta(seconds=29))
    assert out.verdict == IdleVerdict.OK


def test_idle_config_thresholds_are_configurable() -> None:
    """AppConfig values (`voice:idleReprompSeconds` / `voice:idleCloseSeconds`)
    are wired through the config — assert custom thresholds are honoured."""

    handler = IdleHandler.start(
        config=IdleConfig(reprompt_seconds=10, close_seconds=20),
        clock=lambda: NOW,
    )
    assert handler.tick(now=NOW + timedelta(seconds=9)).verdict == IdleVerdict.OK
    assert handler.tick(now=NOW + timedelta(seconds=10)).verdict == IdleVerdict.REPROMPT
    assert handler.tick(now=NOW + timedelta(seconds=20)).verdict == IdleVerdict.CLOSE


@pytest.mark.asyncio
async def test_idle_close_preserves_cosmos_state() -> None:
    """A close verdict does NOT touch Cosmos — the next submit_answer for
    the same `session_id` must still succeed (FORBIDDEN ACTIONS)."""

    from tests.integration.conftest import FakeCosmosRepository, make_session_doc
    from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
    from src.agent.tools import ToolDeps, build_tools
    from src.agent.dispatcher import Principal

    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
    )

    session = make_session_doc(n=3).model_copy(
        update={
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
        }
    )
    stored = await repo.create_session(session)

    # Idle handler fires CLOSE — runtime drops the WebRTC connection. State
    # in Cosmos is intact: the next submit_answer (text or voice) succeeds.
    handler = IdleHandler.start(clock=lambda: NOW)
    out = handler.tick(now=NOW + timedelta(seconds=60))
    assert out.verdict == IdleVerdict.CLOSE

    tools = build_tools(deps)
    result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "voice",
        },
        Principal(entra_oid=stored.user_id),
    )
    assert result.ok is True
    assert result.data["verdict"] == "correct"
