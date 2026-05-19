"""Tool I/O schema tests (TASK-080 / TEST-006).

The contract is structural: the JSON schema produced by every tool
response model must NOT carry a `correct_answer` field (or its synonyms)
at any nesting depth. `extra="forbid"` on every response model is the
typed boundary; this test reflects the schema and asserts the SEC-001
property at the type level.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from src.data.models import (
    BreakdownItem,
    GetResultsResponse,
    ListTopicsResponse,
    QuestionView,
    ResultsSummary,
    SetLanguageResponse,
    StartQuizResponse,
    SubmitAnswerResponse,
    TopicSummary,
)

# Synonyms checked. Mirrors `src/agent/defensive_strip._FORBIDDEN_KEYS`.
FORBIDDEN_KEYS: frozenset[str] = frozenset({"correct_answer", "correctAnswer", "answer_key"})

# Every public tool response model — schema must omit the forbidden keys.
RESPONSE_MODELS: tuple[type[BaseModel], ...] = (
    ListTopicsResponse,
    SetLanguageResponse,
    StartQuizResponse,
    SubmitAnswerResponse,
    GetResultsResponse,
    ResultsSummary,
    BreakdownItem,
    QuestionView,
    TopicSummary,
)


def _collect_property_names(schema: dict[str, Any]) -> set[str]:
    """Walk a JSON schema and return every `properties` key name seen.

    Pydantic v2's schemas put nested model property maps under `$defs`,
    so the walk descends into both the root `properties` and every
    definition's `properties`.
    """

    names: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                names.update(props.keys())
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(schema)
    return names


@pytest.mark.parametrize("model_cls", RESPONSE_MODELS)
def test_response_model_schema_has_no_forbidden_keys(model_cls: type[BaseModel]) -> None:
    schema = model_cls.model_json_schema()
    names = _collect_property_names(schema)
    leaks = names & FORBIDDEN_KEYS
    assert not leaks, (
        f"{model_cls.__name__} schema exposes forbidden answer-key field(s): {sorted(leaks)}"
    )


@pytest.mark.parametrize("model_cls", RESPONSE_MODELS)
def test_response_model_runtime_dump_has_no_forbidden_keys(
    model_cls: type[BaseModel],
) -> None:
    """Defence in depth: any actual runtime dump (model → dict) of a
    populated instance must omit the forbidden keys. Schema docstrings
    intentionally reference `correct_answer` in their warning copy — the
    runtime payload is what matters for SEC-001."""

    sample = _example_instance(model_cls)
    if sample is None:
        pytest.skip(f"no example provided for {model_cls.__name__}")
    payload = sample.model_dump(mode="json", by_alias=True)
    import json

    rendered = json.dumps(payload)
    for forbidden in FORBIDDEN_KEYS:
        assert forbidden not in rendered, (
            f"{model_cls.__name__} runtime dump contains forbidden substring {forbidden!r}"
        )


def _example_instance(model_cls: type[BaseModel]) -> BaseModel | None:
    """Build a minimal valid instance of `model_cls` for runtime tests.

    Models whose construction depends on a Cosmos-backed source (e.g.,
    SubmitAnswerResponse needs a full ResultsSummary) are skipped above
    rather than synthesised here — the runtime dump path is exercised by
    the integration tests under ``tests/integration/``.
    """

    from datetime import datetime, timezone

    from src.data.models import Option, QuestionView, Verdict, SessionStatus

    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    qv = QuestionView(
        question_id="q-1",
        text="?",
        options=[Option(key="A", text="a"), Option(key="B", text="b")],
        difficulty="easy",
    )
    if model_cls is QuestionView:
        return qv
    if model_cls is TopicSummary:
        return TopicSummary(topic_id="t-1", label="Topic", count=5, has_fallback=False)
    if model_cls is ListTopicsResponse:
        return ListTopicsResponse(language="en", topics=[])
    if model_cls is SetLanguageResponse:
        return SetLanguageResponse(user_id="u-1", language="en", updated_at=now)
    if model_cls is BreakdownItem:
        return BreakdownItem(question_id="q-1", verdict=Verdict.CORRECT, score=1.0)
    if model_cls in (ResultsSummary, GetResultsResponse):
        return ResultsSummary(
            session_id="s-1",
            status=SessionStatus.SCORED,
            score=1.0,
            max_score=1.0,
            percentage=100.0,
            is_pass=True,
            pass_threshold_pct=60.0,
            language="en",
            duration_seconds=10,
            breakdown=[],
        )
    if model_cls is StartQuizResponse:
        return StartQuizResponse(
            session_id="s-1",
            question=qv,
            index=1,
            total=1,
            language="en",
            fallback_notice=None,
            time_limit_seconds=600,
            question_started_at=now,
        )
    if model_cls is SubmitAnswerResponse:
        return SubmitAnswerResponse(
            verdict=Verdict.CORRECT,
            score_delta=1.0,
            running_score=1.0,
            index=1,
            total=1,
            next=None,
            done=True,
            results=None,
            question_started_at=None,
        )
    return None


def test_questionview_explicitly_rejects_correct_answer_at_validation() -> None:
    """`extra='forbid'` is the structural boundary — assert it bites."""

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        QuestionView.model_validate(
            {
                "question_id": "az-net-0042-fr",
                "text": "...",
                "options": [{"key": "A", "text": "..."}],
                "difficulty": "easy",
                "correct_answer": ["B"],  # forbidden
            }
        )
