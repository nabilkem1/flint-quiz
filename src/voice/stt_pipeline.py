"""STT streaming integration (TASK-102 / TASK-104).

Only **final** STT transcripts reach the agent's turn loop. Partials
(interim transcripts) may surface to the UX layer as live captions, but
the SEC-001 / NFR-014 contract is that the tool layer — `submit_answer`
in particular — never receives a partial. A partial that races a final
would produce a graded answer the user did not finish saying.

This module is intentionally thin: a dataclass for the canonical
transcript shape, a small router that forwards finals to a callback,
and a validation guard that refuses to dispatch a transcript whose
confidence is below the configured floor (`voice:sttConfidenceFloor`).
The Realtime SDK plumbs partial / final flags onto its events; we
adapt that surface here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Transcript:
    """One per-turn transcript emitted by the Realtime STT.

    ``is_final=True`` is required for the transcript to reach the
    agent's turn loop. ``confidence`` is the STT engine's per-turn
    confidence (0..1); the gating floor lives in AppConfig
    (``voice:sttConfidenceFloor``).

    ``language_hint`` is the STT-detected language. It is **never**
    used to flip ``session.language`` — that change requires an
    explicit ``set_language`` tool call (GOV-027). We surface it so
    telemetry can flag code-switching incidents.
    """

    text: str
    is_final: bool
    confidence: float
    session_id: str
    language_hint: str | None = None
    received_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class STTConfig:
    confidence_floor: float = 0.5
    """Below this floor, even a final transcript triggers a re-prompt
    rather than entering the tool loop (008-api §5.5)."""


class _Emitter(Protocol):
    def emit(self, name: str, properties: dict[str, object]) -> None: ...


FinalHandler = Callable[[Transcript], Awaitable[None]]


class STTRouter:
    """Filter STT events down to **finals that pass the confidence floor**.

    The router does not own the Realtime client; it is fed events by it.
    Production wiring plugs the router into the SDK's transcript event
    sink; tests call :meth:`dispatch` directly with fabricated
    ``Transcript`` objects.
    """

    def __init__(
        self,
        *,
        final_handler: FinalHandler,
        config: STTConfig | None = None,
        emitter: _Emitter | None = None,
    ) -> None:
        self._handle = final_handler
        self._config = config or STTConfig()
        self._emitter = emitter

    async def dispatch(self, transcript: Transcript) -> bool:
        """Forward `transcript` to the final-handler iff it qualifies.

        Returns ``True`` iff the transcript was forwarded. The dropped
        cases emit telemetry but **never** raise — STT noise must not
        break the channel.
        """

        if not transcript.is_final:
            # Partials are observable for UX captions but never reach
            # the agent. We deliberately do not log every partial — the
            # SDK already surfaces them.
            return False

        if not transcript.text or not transcript.text.strip():
            self._emit_drop(transcript, reason="empty_final")
            return False

        if transcript.confidence < self._config.confidence_floor:
            self._emit_drop(transcript, reason="below_confidence_floor")
            logger.info(
                "stt.dropped_low_confidence",
                extra={
                    "session_id": transcript.session_id,
                    "confidence": transcript.confidence,
                    "floor": self._config.confidence_floor,
                },
            )
            return False

        await self._handle(transcript)
        return True

    def _emit_drop(self, transcript: Transcript, *, reason: str) -> None:
        if self._emitter is None:
            return
        self._emitter.emit(
            "voice.stt_drop",
            {
                "session_id": transcript.session_id,
                "reason": reason,
                "confidence": transcript.confidence,
                "is_final": transcript.is_final,
            },
        )


def make_transcript(
    text: str,
    *,
    is_final: bool,
    confidence: float,
    session_id: str,
    language_hint: str | None = None,
) -> Transcript:
    """Tiny factory used by the Realtime SDK adapter (production) and
    tests (synthetic events). Keeps the timestamp injection in one place."""

    return Transcript(
        text=text,
        is_final=is_final,
        confidence=confidence,
        session_id=session_id,
        language_hint=language_hint,
        received_at=datetime.now(tz=timezone.utc),
    )


__all__ = ["STTConfig", "STTRouter", "Transcript", "FinalHandler", "make_transcript"]
