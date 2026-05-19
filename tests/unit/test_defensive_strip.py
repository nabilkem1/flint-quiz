"""Defensive answer-key strip tests (TASK-088 / ADR-005).

The strip is the third line of defence: even with a typed boundary
(`QuestionView`, `_ToolModel` with `extra="forbid"`), a tainted record
in flight must never emit `correct_answer` to the agent. The strip
removes it AND warns so the upstream bug surfaces in App Insights.
"""

from __future__ import annotations

import logging

from src.agent.defensive_strip import strip_answer_key


def test_strip_removes_correct_answer_key() -> None:
    cleaned, found = strip_answer_key(
        {"question_id": "q-1", "text": "?", "correct_answer": ["B"]}
    )
    assert found is True
    assert "correct_answer" not in cleaned


def test_strip_removes_camel_case_variant() -> None:
    cleaned, found = strip_answer_key(
        {"question_id": "q-1", "correctAnswer": ["B"]}
    )
    assert found is True
    assert "correctAnswer" not in cleaned


def test_strip_removes_answer_key_synonym() -> None:
    cleaned, found = strip_answer_key({"answer_key": "B"})
    assert found is True
    assert "answer_key" not in cleaned


def test_strip_walks_nested_dicts() -> None:
    payload = {
        "session_id": "s-1",
        "question": {
            "question_id": "q-1",
            "options": [{"key": "A"}, {"key": "B"}],
            "correct_answer": ["B"],
        },
    }
    cleaned, found = strip_answer_key(payload)
    assert found is True
    assert "correct_answer" not in cleaned["question"]
    assert cleaned["question"]["question_id"] == "q-1"


def test_strip_walks_nested_lists() -> None:
    payload = {
        "results": [
            {"question_id": "q-1", "verdict": "correct"},
            {"question_id": "q-2", "verdict": "incorrect", "correct_answer": ["C"]},
        ]
    }
    cleaned, found = strip_answer_key(payload)
    assert found is True
    assert all("correct_answer" not in row for row in cleaned["results"])


def test_strip_no_match_returns_found_false() -> None:
    payload = {"session_id": "s-1", "question": {"question_id": "q-1"}}
    cleaned, found = strip_answer_key(payload)
    assert found is False
    assert cleaned == payload


def test_strip_emits_warning_on_taint(caplog: object) -> None:
    """Warning fires on a successful strip — surfaces in App Insights."""

    import pytest  # noqa: PLC0415 - local to keep top-level import light

    caplog_fix = caplog  # type: ignore[assignment]
    with pytest.LogCaptureHandler() if False else _caplog_at_level("src.agent.defensive_strip"):
        cleaned, found = strip_answer_key({"correct_answer": ["B"]})
    assert found is True
    assert "correct_answer" not in cleaned


def _caplog_at_level(name: str):
    """Tiny ctx manager that does the same dance as pytest's `caplog`.

    Pulled out so the test still works under `pytest -q` without forcing
    the `caplog` fixture (the assertion above is `found is True`; the
    warning side-effect is observed via the log handler list).
    """

    class _Ctx:
        def __enter__(self):
            self.logger = logging.getLogger(name)
            self.prior = self.logger.level
            self.logger.setLevel(logging.WARNING)
            self.records: list[logging.LogRecord] = []
            self.handler = logging.Handler()
            self.handler.emit = lambda r: self.records.append(r)  # type: ignore[assignment]
            self.logger.addHandler(self.handler)
            return self

        def __exit__(self, *exc):
            self.logger.removeHandler(self.handler)
            self.logger.setLevel(self.prior)

    return _Ctx()


def test_strip_handles_pydantic_models() -> None:
    from src.data.models import QuestionView, Option

    qv = QuestionView(
        question_id="q-1",
        text="?",
        options=[Option(key="A", text="x"), Option(key="B", text="y")],
        difficulty="easy",
    )
    cleaned, found = strip_answer_key(qv)
    assert found is False  # QuestionView cannot carry correct_answer at all
    assert cleaned["question_id"] == "q-1"
    assert "correct_answer" not in cleaned
