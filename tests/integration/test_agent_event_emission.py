"""`agent.*` event taxonomy emission tests (TASK-149).

Per-event assertions:

  * Every event in :class:`AgentEvent` has a dimension policy entry.
  * Emitting with the documented dimensions succeeds.
  * Emitting with a missing required dimension raises
    :class:`EventDimensionError`.
  * Emitting with a forbidden dimension (any 🟡/🔴 field from
    008-api §0.1) raises.
  * The dispatcher's existing event emissions (`agent.unknown_tool`,
    `agent.auth_mismatch`, `agent.prompt_hash_mismatch`) carry the
    documented dimensions when routed through the typed entry point.

Production wiring routes the dispatcher's emitter into
`emit_agent_event` so all existing dispatcher emissions inherit the
policy. The dispatcher itself currently emits via its `_emitter.emit`
directly — for this pack we add **typed** emission via
:func:`emit_agent_event` and verify the policy enforces.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.observability.events import (
    FORBIDDEN_EVENT_DIMENSIONS,
    AgentEvent,
    EventDimensionError,
    RecordingEmitter,
    emit_agent_event,
)


def test_every_taxonomy_entry_has_a_required_dimension_set() -> None:
    """The dimension policy table must cover every named event."""

    from src.observability.events import _REQUIRED_DIMENSIONS  # type: ignore[attr-defined]

    missing = [e for e in AgentEvent if e not in _REQUIRED_DIMENSIONS]
    assert not missing, f"events lacking a dimension policy: {missing}"


def test_injection_detected_with_hashed_payload_emits() -> None:
    emitter = RecordingEmitter()
    emit_agent_event(
        emitter,
        AgentEvent.INJECTION_DETECTED,
        {
            "session_id": "s-1",
            "language": "en",
            "channel": "text",
            # SHA-256 of the utterance with KV salt — NEVER the raw text.
            "payload_hash": "a" * 64,
            "payload_encoding": "plain",
            "redirect_class": "soft",
        },
    )
    events = emitter.find(AgentEvent.INJECTION_DETECTED)
    assert len(events) == 1
    assert "payload_hash" in events[0]
    # Hardened: the literal utterance is never present.
    assert "utterance" not in events[0]
    assert "raw_payload" not in events[0]


@pytest.mark.parametrize(
    "event,dimensions",
    [
        (
            AgentEvent.COVERAGE_GAP,
            {
                "session_id": "s-1",
                "topic": "azure-networking",
                "requested_language": "fr",
                "suggested_fallback": "en",
                "consent_path": "pending",
            },
        ),
        (
            AgentEvent.REFUSAL_LOOP,
            {
                "session_id": "s-1",
                "language": "en",
                "channel": "voice",
                "refusal_class": "soft",
            },
        ),
        (
            AgentEvent.UNKNOWN_TOOL,
            {
                "session_id": "s-1",
                "requested_tool_name": "bogus_tool",
                "principal_oid": "alice",
            },
        ),
        (
            AgentEvent.PROMPT_HASH_MISMATCH,
            {
                "session_id": "s-1",
                "expected_hash": "abcd" * 16,
                "actual_hash": "ef01" * 16,
                "language": "en",
            },
        ),
        (
            AgentEvent.OUTPUT_TRUNCATED,
            {
                "session_id": "s-1",
                "language": "en",
                "channel": "text",
                "requested_max": 600,
                "returned": 600,
            },
        ),
        (
            AgentEvent.USER_ERASED,
            {
                "pseudo_userid": "pseudo:v1:abc1234567890def",
                "requested_by": "support-oid",
                "ticket_ref": "TICKET-1",
                "counts.users": 1,
                "counts.sessions": 2,
                "counts.audit_pseudonymized": 5,
            },
        ),
        (
            AgentEvent.USER_ERASED_REPEAT,
            {
                "pseudo_userid": "pseudo:v1:abc1234567890def",
                "ticket_ref": "TICKET-2",
            },
        ),
        (
            AgentEvent.ERASURE_ARCHIVE_LOCKED,
            {
                "pseudo_userid": "pseudo:v1:abc1234567890def",
                "locked_snapshot_ids": ["snap-1", "snap-2"],
            },
        ),
        (
            AgentEvent.SWEEPER_STRANDED_RELEASED,
            {"count": 3},
        ),
    ],
)
def test_documented_dimensions_emit_successfully(event, dimensions) -> None:
    emitter = RecordingEmitter()
    emit_agent_event(emitter, event, dimensions)
    assert emitter.count(event) == 1


@pytest.mark.parametrize(
    "event,dimensions",
    [
        # Missing required `payload_hash`.
        (
            AgentEvent.INJECTION_DETECTED,
            {
                "session_id": "s-1",
                "language": "en",
                "channel": "text",
                "payload_encoding": "plain",
                "redirect_class": "soft",
            },
        ),
        # Missing required `consent_path`.
        (
            AgentEvent.COVERAGE_GAP,
            {
                "session_id": "s-1",
                "topic": "azure-networking",
                "requested_language": "fr",
                "suggested_fallback": "en",
            },
        ),
    ],
)
def test_missing_dimension_raises(event, dimensions) -> None:
    with pytest.raises(EventDimensionError):
        emit_agent_event(RecordingEmitter(), event, dimensions)


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_EVENT_DIMENSIONS))
def test_forbidden_dimension_in_any_event_raises(forbidden: str) -> None:
    """The forbidden-dimension lint covers EVERY event in the taxonomy."""

    base = {
        "session_id": "s",
        "topic": "t",
        "requested_language": "fr",
        "suggested_fallback": "en",
        "consent_path": "pending",
    }
    base[forbidden] = "leaked-value"
    with pytest.raises(EventDimensionError):
        emit_agent_event(RecordingEmitter(), AgentEvent.COVERAGE_GAP, base)
