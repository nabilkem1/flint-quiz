"""Realtime endpoint wiring (TASK-100 / TASK-101 / TASK-106 / TASK-107).

The voice channel is a *second entry point* to the same `QuizAgent`
instance the Playground uses. Nothing in this module re-registers
tools — the dispatcher (`src/agent/dispatcher.py`) is reused. Durable
state stays in Cosmos; channel is metadata.

What this module owns:

  1. **Per-language voice selection.** On every connection (fresh or
     resumed), the runtime resolves the voice for the session's
     persisted language from AppConfig (`voices:{lang}`). The voice
     **does not flip** if the user briefly code-switches.
  2. **Channel propagation.** Every dispatched tool call carries
     ``channel="voice"`` in its args and any emitted span / event.
     The dispatcher and tools already accept this dimension; this
     module just makes sure it lands.
  3. **Channel-switch tolerance.** On a connection that resumes an
     existing `session_id`, the runtime calls `resume_from_session`
     (004-agent-framework TASK-068). The agent re-acknowledges in the
     persisted language; durable state is untouched.
  4. **Latency span tagging.** Each tool dispatch is wrapped in a
     `voice.tool_call` span carrying the channel and language
     dimensions so the workbook (TASK-109) and alert can group by
     channel. The runtime does NOT enforce the budget — it surfaces
     the latency; the alert fires server-side from App Insights.

What this module does NOT own:

  * The WebRTC handshake (Realtime SDK).
  * STT / TTS bytes (the SDK streams them; we adapt).
  * Tool bodies (005-tools).
  * Prompt composition (004-agent-framework TASK-071).

Design: this is a small façade that the production Realtime SDK
adapter calls into. Tests construct it with in-memory fakes (no
WebRTC) and exercise the channel-switch, idle, and cap paths directly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from src.agent.dispatcher import Dispatcher, Principal, ToolResult
from src.agent.resumption import ResumeContext
from src.data.models import Channel, SessionDoc

logger = logging.getLogger(__name__)

# Default voice map (mirrors `infra/modules/realtime.bicep`). AppConfig
# overrides at runtime; this constant is the build-time fallback so a
# missing key never breaks the connection.
DEFAULT_VOICE_BY_LANGUAGE: dict[str, str] = {
    "en": "alloy",
    "fr": "shimmer",
    "es": "verse",
}


class VoiceConfigProvider(Protocol):
    """Resolves voice + cap config from AppConfig.

    The production wiring is a thin `AzureAppConfigurationClient` shim;
    tests pass a dict-backed fake. Resolution is per-connection — a
    voice-catalog update never requires an agent restart.
    """

    def voice_for(self, language: str) -> str: ...

    def session_cap_minutes(self) -> int: ...

    def idle_reprompt_seconds(self) -> int: ...

    def idle_close_seconds(self) -> int: ...

    def stt_confidence_floor(self) -> float: ...


@dataclass(frozen=True, slots=True)
class StaticVoiceConfig:
    """In-memory `VoiceConfigProvider` for tests + the local-dev fallback."""

    voices: Mapping[str, str] = (  # type: ignore[assignment]
        # Frozen-dict equivalent; the runtime never mutates this mapping.
        # `dict` is good enough here because the dataclass is frozen.
    )
    max_session_minutes: int = 30
    reprompt_seconds: int = 30
    close_seconds: int = 60
    confidence_floor: float = 0.5

    def voice_for(self, language: str) -> str:
        if self.voices:
            return self.voices.get(language) or DEFAULT_VOICE_BY_LANGUAGE.get(
                language, "alloy"
            )
        return DEFAULT_VOICE_BY_LANGUAGE.get(language, "alloy")

    def session_cap_minutes(self) -> int:
        return self.max_session_minutes

    def idle_reprompt_seconds(self) -> int:
        return self.reprompt_seconds

    def idle_close_seconds(self) -> int:
        return self.close_seconds

    def stt_confidence_floor(self) -> float:
        return self.confidence_floor


class EventEmitter(Protocol):
    def emit(self, name: str, properties: Mapping[str, Any]) -> None: ...


class _NullEmitter:
    def emit(self, name: str, properties: Mapping[str, Any]) -> None:  # pragma: no cover
        return None


@dataclass(frozen=True, slots=True)
class VoiceSession:
    """The per-connection handle the runtime hands back to the SDK adapter.

    All fields are 🟢 LLM-OK and safe to log. The handle is
    deliberately small — durable state stays in Cosmos.
    """

    session_id: str
    user_id: str
    language: str
    voice: str
    channel: Channel  # always Channel.VOICE here
    is_resume: bool
    is_channel_switch: bool
    next_question_id: str | None
    answered_count: int
    total: int


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class RealtimeRuntime:
    """Façade the Realtime SDK adapter delegates into.

    Three responsibilities:

      * :meth:`bind_session` resolves the per-connection ``VoiceSession``
        (voice + language + resume context). Called once per WebRTC
        connect.
      * :meth:`dispatch_tool` is the single tool-call entry point the
        adapter uses; it forwards to the shared :class:`Dispatcher`
        with the correct channel metadata and timing span.
      * :meth:`make_principal` is a tiny helper that materialises the
        :class:`Principal` from the channel-layer auth claims.
    """

    def __init__(
        self,
        *,
        dispatcher: Dispatcher,
        config_provider: VoiceConfigProvider,
        emitter: EventEmitter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dispatcher = dispatcher
        self._config = config_provider
        self._emitter = emitter or _NullEmitter()
        self._clock = clock

    # ----- Lifecycle ----------------------------------------------------

    def bind_session(
        self,
        *,
        session: SessionDoc,
        resume_context: ResumeContext | None,
    ) -> VoiceSession:
        """Build the per-connection handle.

        `resume_context` is the result of `resume_from_session` (TASK-068);
        it carries the channel-switch flag, the next-question pointer,
        and the language pin. For a brand-new voice session (no row yet),
        the caller passes `None` and we fall back to the SessionDoc fields.
        """

        language = session.language
        voice = self._config.voice_for(language)

        is_resume = resume_context is not None
        is_switch = bool(resume_context and resume_context.is_channel_switch)
        next_qid = (
            resume_context.next_question_id
            if resume_context is not None
            else (
                session.shuffled_ids[session.current_index]
                if session.current_index < len(session.shuffled_ids)
                else None
            )
        )
        answered_count = len(session.answers)

        handle = VoiceSession(
            session_id=session.id,
            user_id=session.user_id,
            language=language,
            voice=voice,
            channel=Channel.VOICE,
            is_resume=is_resume,
            is_channel_switch=is_switch,
            next_question_id=next_qid,
            answered_count=answered_count,
            total=len(session.shuffled_ids),
        )
        self._emitter.emit(
            "voice.session_bound",
            {
                "session_id": handle.session_id,
                "language": handle.language,
                "voice": handle.voice,
                "is_resume": handle.is_resume,
                "is_channel_switch": handle.is_channel_switch,
            },
        )
        if is_switch:
            logger.info(
                "voice.channel_switch.text_to_voice",
                extra={
                    "session_id": handle.session_id,
                    "language": handle.language,
                },
            )
        return handle

    def make_principal(self, *, entra_oid: str) -> Principal:
        return Principal(entra_oid=entra_oid)

    # ----- Tool dispatch ------------------------------------------------

    async def dispatch_tool(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        handle: VoiceSession,
    ) -> ToolResult:
        """Forward a voice-channel tool call through the shared dispatcher.

        The args are **stamped** with ``channel="voice"`` so the
        `submit_answer` body records the channel on the answer row and
        the `grading_event` carries the correct dimension (TASK-106 /
        008-api §4.5). Anything the SDK adapter passed in `args["channel"]`
        is overwritten — the runtime is authoritative on channel.
        """

        # Stamp the channel — the runtime is authoritative on that
        # dimension. `user_id` stays the caller's responsibility because
        # not every tool accepts it (`submit_answer` derives owner from
        # the session row), and adding it unconditionally would trip the
        # `extra="forbid"` request model.
        merged_args = dict(args)
        merged_args["channel"] = Channel.VOICE.value

        start = self._clock()
        principal = self.make_principal(entra_oid=handle.user_id)
        result = await self._dispatcher.dispatch(tool_name, merged_args, principal)
        elapsed_ms = int((self._clock() - start) * 1000)

        # Voice-specific span. The dispatcher already emits
        # `agent.dispatch.<tool>` with latency; this companion event
        # carries the **channel** dimension so the voice workbook
        # (TASK-109) and alert can filter by channel without joining
        # against the session row.
        self._emitter.emit(
            "voice.tool_call",
            {
                "session_id": handle.session_id,
                "tool": tool_name,
                "language": handle.language,
                "channel": handle.channel.value,
                "latency_ms": elapsed_ms,
                "ok": result.ok,
            },
        )
        return result


# ---------------------------------------------------------------------------
# Per-language phrasing helpers
# ---------------------------------------------------------------------------


def select_voice(
    *,
    language: str,
    config: VoiceConfigProvider,
) -> str:
    """Standalone helper used by the SDK adapter (and tests).

    The voice never depends on the request-time channel — it depends on
    the session's persisted language. A code-switched utterance does
    **not** change the voice for the rest of the session.
    """

    return config.voice_for(language)


__all__ = [
    "DEFAULT_VOICE_BY_LANGUAGE",
    "EventEmitter",
    "RealtimeRuntime",
    "StaticVoiceConfig",
    "VoiceConfigProvider",
    "VoiceSession",
    "select_voice",
]
