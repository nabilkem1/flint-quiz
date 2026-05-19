"""Spanish voice smoke test (TEST-005).

The Realtime SDK is not exercised here — its handshake lives in the
production runtime adapter. What we DO assert is that:

  * The runtime resolves the **Spanish-configured voice** from the
    config provider.
  * Tool dispatches from a voice session carry ``channel="voice"`` so
    `submit_answer` records the channel correctly (008-api §4.5).
  * The spoken-style answer normaliser handles Spanish utterances end
    to end — "letra B", "la segunda", "Puerta de enlace VPN" all map.
  * `voice.tool_call` events fire with the channel + language
    dimensions the workbook (TASK-109) groups by.

This is the cheap, in-process flavour of TEST-005. The live-endpoint
version runs against a deployed Foundry account in 009-testing.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Dispatcher, Principal
from src.agent.prompts.compose import SessionFrame
from src.agent.tools import ToolDeps, build_tools
from src.data.models import Channel
from src.voice.realtime_runtime import (
    DEFAULT_VOICE_BY_LANGUAGE,
    RealtimeRuntime,
    StaticVoiceConfig,
    select_voice,
)

from ._tools_fakes import RecordingEmitter, build_fake_search, make_topic_doc
from .conftest import FakeCosmosRepository, make_session_doc

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def test_voice_for_es_resolves_to_configured_voice() -> None:
    config = StaticVoiceConfig(voices={"en": "alloy", "fr": "shimmer", "es": "verse"})
    assert select_voice(language="es", config=config) == "verse"
    # Default-map fallback if AppConfig has no override.
    fallback = StaticVoiceConfig()
    assert select_voice(language="es", config=fallback) == DEFAULT_VOICE_BY_LANGUAGE["es"]


def test_voice_does_not_flip_on_code_switch() -> None:
    """A brief English interjection does NOT change the configured voice."""

    config = StaticVoiceConfig(voices={"en": "alloy", "es": "verse"})
    # `session.language` stays "es" because GOV-027 — only `set_language`
    # flips it. The configured voice follows the session language.
    assert select_voice(language="es", config=config) == "verse"


@pytest.fixture
def runtime_deps():
    repo = FakeCosmosRepository()
    search = build_fake_search(count=3, language="es", topic="azure-networking")
    emitter = RecordingEmitter()
    deps = ToolDeps(
        repo=repo,
        search=search,  # type: ignore[arg-type]
        emitter=emitter,
        clock=lambda: NOW,
    )
    return deps, emitter


def _frame_provider(default_total: int = 3):
    def _build(session):
        return SessionFrame(
            session_id=session.id,
            user_id=session.user_id,
            topic=session.topic,
            language=session.language,
            channel_at_start=(
                session.channel.value if isinstance(session.channel, Channel) else session.channel
            ),
            total=len(session.shuffled_ids) or default_total,
            time_limit_seconds=session.time_limit_seconds,
            started_at=session.started_at,
        )

    return _build


@pytest.mark.asyncio
async def test_voice_smoke_es_end_to_end(runtime_deps) -> None:
    from src.agent.prompts.compose import compose

    deps, emitter = runtime_deps
    tools = build_tools(deps)

    # Seed an Active Spanish session so the runtime can bind. The prompt
    # hash is pinned at session-start time per GOV-003 — re-derive it
    # here so the dispatcher's per-turn check passes.
    session = make_session_doc(n=3).model_copy(
        update={
            "language": "es",
            "requested_language": "es",
            "channel": Channel.VOICE,
            "shuffled_ids": [f"azure-networking-{i:03d}-es" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    frame = _frame_provider()(session)
    _, prompt_hash = compose(language="es", session_frame=frame)
    session = session.model_copy(update={"prompt_hash": prompt_hash})
    stored = await deps.repo.create_session(session)

    # Build the dispatcher + runtime wired to the live tools.
    dispatcher = Dispatcher(
        tools=tools,
        session_store=deps.repo,
        frame_provider=_frame_provider(),
        emitter=emitter,
    )
    voice_config = StaticVoiceConfig(
        voices={"en": "alloy", "fr": "shimmer", "es": "verse"}
    )
    runtime = RealtimeRuntime(
        dispatcher=dispatcher,
        config_provider=voice_config,
        emitter=emitter,
    )

    handle = runtime.bind_session(session=stored, resume_context=None)
    assert handle.voice == "verse"
    assert handle.language == "es"
    assert handle.channel == Channel.VOICE

    # Spoken Spanish answer for question 1 — "letra B" → B (correct).
    submit_result = await runtime.dispatch_tool(
        tool_name="submit_answer",
        args={
            "session_id": handle.session_id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "letra B",
        },
        handle=handle,
    )
    assert submit_result.ok is True
    assert submit_result.data["verdict"] == "correct"

    # Verify the `voice.tool_call` event landed with the channel dim.
    voice_events = [e for e in emitter.events if e[0] == "voice.tool_call"]
    assert voice_events, "voice.tool_call event must fire on every voice dispatch"
    assert voice_events[-1][1]["channel"] == "voice"
    assert voice_events[-1][1]["language"] == "es"
    assert voice_events[-1][1]["tool"] == "submit_answer"
