"""Pydantic model round-trip tests (TASK-045, 008-api §0.4).

Two invariants:

1. ``from_cosmos(to_cosmos(m)) == m`` — every Cosmos-bound model round-trips
   through its camelCase wire shape without losing data. The
   ``populate_by_name + alias_generator=to_camel`` pairing is the casing
   bridge; if it drifts, every dispute-resolution query breaks.

2. ``from_tool(to_tool(m)) == m`` — tool-I/O models round-trip through their
   snake_case shape.

Also asserts the SEC-001 boundary at the type level: ``QuestionView``
forbids ``correct_answer`` even at construction time; ``AnswerKey`` raises
on every JSON-serializer entry point.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.data.models import (
    Answer,
    AnswerKey,
    AuditEvent,
    BreakdownItem,
    Channel,
    Option,
    QuestionView,
    ResultsSummary,
    SessionDoc,
    SessionStatus,
    TopicDoc,
    UserDoc,
    Verdict,
)


NOW = datetime(2026, 5, 17, 12, 34, 56, tzinfo=timezone.utc)


def _session_doc() -> SessionDoc:
    return SessionDoc(
        id="sess-1",
        user_id="user-1",
        topic="azure-networking",
        language="fr",
        requested_language="fr",
        seed="3f1e9a7c4b2d8e60",
        shuffled_ids=["az-net-0042-fr", "az-net-0010-fr"],
        current_index=1,
        answers=[
            Answer(
                question_id="az-net-0042-fr",
                received_raw="la deuxième",
                received_normalized="B",
                verdict=Verdict.CORRECT,
                score_delta=1.0,
                answered_at=NOW,
                channel=Channel.VOICE,
                latency_ms=142,
            )
        ],
        score=1.0,
        max_score=2.0,
        status=SessionStatus.ACTIVE,
        started_at=NOW,
        question_started_at=NOW,
        time_limit_seconds=600,
        channel=Channel.VOICE,
        etag='"00000000-0000-0000-fe9d-2ad6a08e01dc"',
    )


# ---------------------------------------------------------------------------
# Cosmos camelCase round-trip
# ---------------------------------------------------------------------------


def test_sessiondoc_round_trip_through_cosmos_wire() -> None:
    original = _session_doc()
    wire = original.model_dump(by_alias=True, exclude_none=True, mode="json")
    # Wire keys are camelCase.
    assert "userId" in wire
    assert "shuffledIds" in wire
    assert "_etag" in wire
    assert "questionStartedAt" in wire
    assert "user_id" not in wire
    # Round-trip preserves the model.
    restored = SessionDoc.model_validate(wire)
    assert restored == original


def test_userdoc_round_trip_through_cosmos_wire() -> None:
    original = UserDoc(
        id="user-1",
        user_id="user-1",
        language="fr",
        detected_language="fr",
        explicitly_set=True,
        created_at=NOW,
        updated_at=NOW,
    )
    wire = original.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert "detectedLanguage" in wire
    assert "explicitlySet" in wire
    restored = UserDoc.model_validate(wire)
    assert restored == original


def test_topicdoc_round_trip_through_cosmos_wire() -> None:
    original = TopicDoc(
        id="azure-networking",
        topic_id="azure-networking",
        labels={"en": "Azure Networking", "fr": "Réseau Azure"},
        counts={"en": 120, "fr": 85},
        default_language="en",
        enabled=True,
        updated_at=NOW,
    )
    wire = original.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert "topicId" in wire
    assert "defaultLanguage" in wire
    restored = TopicDoc.model_validate(wire)
    assert restored == original


def test_auditevent_round_trip_through_cosmos_wire() -> None:
    original = AuditEvent(
        id="audit-1",
        session_id="sess-1",
        user_id="user-1",
        question_id="az-net-0042-fr",
        language="fr",
        channel=Channel.VOICE,
        expected=["B"],
        received="B",
        received_raw="la deuxième",
        verdict=Verdict.CORRECT,
        score_delta=1.0,
        latency_ms=142,
        timestamp=NOW,
    )
    wire = original.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert "sessionId" in wire
    assert "receivedRaw" in wire
    assert "scoreDelta" in wire
    restored = AuditEvent.model_validate(wire)
    assert restored == original


# ---------------------------------------------------------------------------
# Tool-I/O snake_case round-trip
# ---------------------------------------------------------------------------


def test_questionview_round_trip_snake_case() -> None:
    qv = QuestionView(
        question_id="az-net-0042-fr",
        text="Quel service Azure ...",
        options=[Option(key="A", text="opt A"), Option(key="B", text="opt B")],
        difficulty="medium",
    )
    wire = qv.model_dump(mode="json")
    # snake_case on the wire (008-api §0.4).
    assert "question_id" in wire
    assert "questionId" not in wire
    restored = QuestionView.model_validate(wire)
    assert restored == qv


def test_resultssummary_round_trip_snake_case_with_pass_alias() -> None:
    summary = ResultsSummary(
        session_id="sess-1",
        status=SessionStatus.SCORED,
        score=4.0,
        max_score=5.0,
        percentage=80.0,
        is_pass=True,
        pass_threshold_pct=60.0,
        language="fr",
        duration_seconds=412,
        breakdown=[
            BreakdownItem(question_id="az-net-0042-fr", verdict=Verdict.CORRECT, score=1.0),
            BreakdownItem(question_id="az-net-0010-fr", verdict=Verdict.INCORRECT, score=0.0),
        ],
    )
    wire = summary.model_dump(by_alias=True, mode="json")
    # `pass` is a Python keyword; wire JSON must still carry the literal key.
    assert "pass" in wire
    assert wire["pass"] is True
    restored = ResultsSummary.model_validate(wire)
    assert restored == summary


# ---------------------------------------------------------------------------
# SEC-001 boundary at the type level (ADR-005)
# ---------------------------------------------------------------------------


def test_questionview_forbids_correct_answer_field() -> None:
    with pytest.raises(ValidationError):
        QuestionView.model_validate(
            {
                "question_id": "az-net-0042-fr",
                "text": "...",
                "options": [],
                "difficulty": "easy",
                "correct_answer": ["B"],  # forbidden
            }
        )


def test_answerkey_has_no_json_serializer() -> None:
    """AnswerKey is a frozen dataclass — no Pydantic-style dump method exists.

    The class-level absence of ``model_dump`` / ``model_dump_json`` is the
    load-bearing boundary: an upstream contributor who reaches for those
    common names hits ``AttributeError`` rather than getting a populated
    dict back. That is the structural guarantee SEC-001 / ADR-005 relies on.
    """

    key = AnswerKey(question_id="qid", correct=frozenset({"B"}), score_weight=1.0)
    assert not hasattr(key, "model_dump")
    assert not hasattr(key, "model_dump_json")
    assert not hasattr(key, "json")


def test_answerkey_repr_masks_correct_set() -> None:
    key = AnswerKey(question_id="qid", correct=frozenset({"B"}), score_weight=1.0)
    repr_str = repr(key)
    assert "B" not in repr_str
    assert "<redacted>" in repr_str


def test_answerkey_not_iterable_into_json_dumps() -> None:
    """``json.dumps(answer_key)`` itself raises — the value is not a Mapping.

    The AST lint (TASK-125) blocks ``asdict(AnswerKey...)`` callsites
    elsewhere; this test asserts the simpler "naive json.dumps leaks the
    key" footgun is closed by the type system alone.
    """

    key = AnswerKey(question_id="qid", correct=frozenset({"B"}), score_weight=1.0)
    with pytest.raises(TypeError):
        json.dumps(key)
