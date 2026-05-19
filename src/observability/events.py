"""Typed event emitters with dimension policy (TASK-141 / TASK-149).

This module is the **single emission path** for every structured event
the Flint Quiz agent surfaces. The dimension policy in
``specs/008-api-contracts.md §0.1`` and the ``agent.*`` taxonomy in
``tasks/008-observability.md`` are encoded here as Pydantic-style
typed dataclasses — anything outside the documented dimension set is
rejected at emit time so a tactical "let's just add this field"
review note becomes a build break.

Two flavours of emitter:

  * :class:`NullEmitter`     — production fallback when telemetry is
    not configured (also used by the dispatcher's default).
  * :class:`RecordingEmitter` — tests; captures every emission for
    assertions.

Production wiring binds an App Insights / OpenTelemetry sink to the
:class:`EventEmitter` protocol; the agent factory passes that sink
into the dispatcher / tool deps / erasure service.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public event taxonomy (TASK-149 / 008-api §4.5.1)
# ---------------------------------------------------------------------------


class AgentEvent(str, Enum):
    """Canonical names for the structured custom events.

    Every emission path uses these constants — never a string literal
    — so the lint in this module's ``_REQUIRED_DIMENSIONS`` table is
    the single source of truth that ties name to required fields.
    """

    GRADING_EVENT = "grading_event"

    # `agent.*` taxonomy (008-observability TASK-149)
    INJECTION_DETECTED = "agent.injection_detected"
    COVERAGE_GAP = "agent.coverage_gap"
    REFUSAL_LOOP = "agent.refusal_loop"
    UNKNOWN_TOOL = "agent.unknown_tool"
    PROMPT_HASH_MISMATCH = "agent.prompt_hash_mismatch"
    PROMPT_HASH_MISSING = "agent.prompt_hash_missing"
    OUTPUT_TRUNCATED = "agent.output_truncated"
    AUTH_MISMATCH = "agent.auth_mismatch"
    TTS_STRIP = "agent.tts_strip"
    USER_ERASED = "audit.user_erased"
    USER_ERASED_REPEAT = "audit.user_erased.repeat"
    ERASURE_ARCHIVE_LOCKED = "audit.erasure_archive_locked"
    ERASURE_DENIED = "audit.erasure_denied"

    SWEEPER_STRANDED_RELEASED = "sweeper.stranded_released"
    SWEEPER_EXPIRED_SWEPT = "sweeper.expired_swept"
    SWEEPER_PAUSED_SWEPT = "sweeper.paused_swept"


# ---------------------------------------------------------------------------
# Dimension policy
# ---------------------------------------------------------------------------

# Required-dimension sets per event name. The policy below is the
# **literal** dimensions documented in 008-api §4.5.1 and TASK-149's
# table. Adding an event = add a row here AND publish a builder. The
# lint in :func:`_require_dimensions` ensures emissions cannot drift.
_REQUIRED_DIMENSIONS: dict[AgentEvent, frozenset[str]] = {
    AgentEvent.GRADING_EVENT: frozenset({
        "session_id", "question_id", "user_id", "language",
        "received", "verdict", "channel", "score_delta",
        "latency_ms", "timestamp",
    }),
    AgentEvent.INJECTION_DETECTED: frozenset({
        "session_id", "language", "channel", "payload_hash",
        "payload_encoding", "redirect_class",
    }),
    AgentEvent.COVERAGE_GAP: frozenset({
        "session_id", "topic", "requested_language",
        "suggested_fallback", "consent_path",
    }),
    AgentEvent.REFUSAL_LOOP: frozenset({
        "session_id", "language", "channel", "refusal_class",
    }),
    AgentEvent.UNKNOWN_TOOL: frozenset({
        "session_id", "requested_tool_name", "principal_oid",
    }),
    AgentEvent.PROMPT_HASH_MISMATCH: frozenset({
        "session_id", "expected_hash", "actual_hash", "language",
    }),
    AgentEvent.PROMPT_HASH_MISSING: frozenset({
        "session_id", "tool",
    }),
    AgentEvent.OUTPUT_TRUNCATED: frozenset({
        "session_id", "language", "channel", "requested_max", "returned",
    }),
    AgentEvent.AUTH_MISMATCH: frozenset({"tool", "principal_oid_prefix"}),
    AgentEvent.TTS_STRIP: frozenset({"session_id", "language", "stripped_chars"}),
    AgentEvent.USER_ERASED: frozenset({
        "pseudo_userid", "requested_by", "ticket_ref",
        "counts.users", "counts.sessions", "counts.audit_pseudonymized",
    }),
    AgentEvent.USER_ERASED_REPEAT: frozenset({"pseudo_userid", "ticket_ref"}),
    AgentEvent.ERASURE_ARCHIVE_LOCKED: frozenset({
        "pseudo_userid", "locked_snapshot_ids",
    }),
    AgentEvent.ERASURE_DENIED: frozenset({
        "principal_oid_prefix", "reason",
    }),
    AgentEvent.SWEEPER_STRANDED_RELEASED: frozenset({"count"}),
    AgentEvent.SWEEPER_EXPIRED_SWEPT: frozenset({"count"}),
    AgentEvent.SWEEPER_PAUSED_SWEPT: frozenset({"count"}),
}


# Fields that MUST NEVER appear in any event payload (008-api §0.1 🟡/🔴).
# The lint runs at emit time — a missed PR that adds one of these as a
# dimension fails the build (via tests) and breaks runtime (via the
# emitter's exception).
FORBIDDEN_EVENT_DIMENSIONS: frozenset[str] = frozenset({
    "correct_answer",
    "correctAnswer",
    "answer_key",
    "expected",
    "received_raw",
    "receivedRaw",
    "_etag",
})


# ---------------------------------------------------------------------------
# Emitter protocol
# ---------------------------------------------------------------------------


class EventEmitter(Protocol):
    """Sink the agent uses to publish events.

    Production binds this to a thin App Insights adapter (sends each
    emission as a `customEvents` row). Tests bind :class:`RecordingEmitter`.
    """

    def emit(self, name: str, properties: Mapping[str, Any]) -> None: ...


class NullEmitter:
    """No-op emitter — the dispatcher and tool deps default to this."""

    def emit(self, name: str, properties: Mapping[str, Any]) -> None:  # pragma: no cover
        return None


@dataclass
class RecordingEmitter:
    """Test sink — captures every event for later assertion.

    The :class:`AgentEvent` taxonomy is enforced at emission time, not
    here; the recorder simply observes.
    """

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, name: str, properties: Mapping[str, Any]) -> None:
        self.events.append((name, dict(properties)))

    def count(self, name: str | AgentEvent) -> int:
        target = name.value if isinstance(name, AgentEvent) else name
        return sum(1 for n, _ in self.events if n == target)

    def find(self, name: str | AgentEvent) -> list[dict[str, Any]]:
        target = name.value if isinstance(name, AgentEvent) else name
        return [p for n, p in self.events if n == target]


# ---------------------------------------------------------------------------
# Public emission helpers
# ---------------------------------------------------------------------------


def emit_grading_event(
    emitter: EventEmitter,
    *,
    session_id: str,
    question_id: str,
    user_id: str,
    language: str,
    received: str,
    verdict: str,
    channel: str,
    score_delta: float,
    latency_ms: int,
    timestamp: str,
) -> None:
    """Emit one ``grading_event`` per persisted answer (008-api §4.5.1).

    The signature is **kwargs-only** so a positional misuse cannot
    swap `received` with `received_raw`. The forbidden-field lint
    inside :func:`_emit_with_policy` rejects any extra dimension that
    sneaks in via a future kwarg expansion.
    """

    dims: dict[str, Any] = {
        "session_id": session_id,
        "question_id": question_id,
        "user_id": user_id,
        "language": language,
        "received": received,
        "verdict": verdict,
        "channel": channel,
        "score_delta": score_delta,
        "latency_ms": latency_ms,
        "timestamp": timestamp,
    }
    _emit_with_policy(emitter, AgentEvent.GRADING_EVENT, dims)


def emit_agent_event(
    emitter: EventEmitter,
    event: AgentEvent,
    dimensions: Mapping[str, Any],
) -> None:
    """Generic typed entry point for the ``agent.*`` taxonomy.

    Callers pass the canonical :class:`AgentEvent` value plus a
    dimension map. The policy table enforces that **every** required
    dimension is present and **no** forbidden dimension leaks through.
    """

    _emit_with_policy(emitter, event, dict(dimensions))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _emit_with_policy(
    emitter: EventEmitter,
    event: AgentEvent,
    dimensions: dict[str, Any],
) -> None:
    """Apply the dimension policy then dispatch.

    Three guarantees:

      1. **Required dimensions present** — missing keys raise
         :class:`EventDimensionError`.
      2. **Forbidden dimensions absent** — `correct_answer`, `expected`,
         `_etag`, etc. — same exception type.
      3. **Stable name on the wire** — emission uses ``event.value``,
         not a string literal, so renames at the source propagate.
    """

    required = _REQUIRED_DIMENSIONS.get(event, frozenset())
    missing = required - dimensions.keys()
    if missing:
        raise EventDimensionError(
            f"event {event.value!r} missing required dimensions: {sorted(missing)}"
        )

    leaks = FORBIDDEN_EVENT_DIMENSIONS & dimensions.keys()
    if leaks:
        # We refuse to emit. A leak here means SEC-001 was about to be
        # bypassed on a telemetry surface — fail loud, never silently
        # drop.
        raise EventDimensionError(
            f"event {event.value!r} carries forbidden dimensions (SEC-001 violation): "
            f"{sorted(leaks)}"
        )

    emitter.emit(event.value, dimensions)


class EventDimensionError(ValueError):
    """Raised when an emission violates the dimension policy.

    Failing emission is preferred to silent corruption — every
    structured event must be either complete-and-safe or refused.
    """


__all__ = [
    "AgentEvent",
    "EventDimensionError",
    "EventEmitter",
    "FORBIDDEN_EVENT_DIMENSIONS",
    "NullEmitter",
    "RecordingEmitter",
    "emit_agent_event",
    "emit_grading_event",
]
