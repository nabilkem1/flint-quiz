"""Server-side per-question + per-quiz timer helpers (TASK-090 / NFR-004).

The session row is the authoritative timing source — the agent and the
client are never trusted to enforce time. These helpers compute three
verdicts the tool layer's `submit_answer` consumes:

* `QuizExpired`    — `now - session.started_at > time_limit_seconds`.
                     The session must flip to `Expired`, remaining
                     questions auto-graded as `unanswered`, and the
                     caller returns the final results envelope.
* `QuestionExpired` — only the per-question budget elapsed. The current
                      question is auto-graded `unanswered`; the quiz
                      continues.
* `OK`              — neither timer elapsed.

Per the FORBIDDEN ACTIONS, this module performs **no** writes; the tool
layer reconciles state with the repository. The clock is injected so tests
can pin time deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from src.data.models import SessionDoc


def utc_now() -> datetime:
    """Return the current UTC time. Pulled out as a function so tests can
    monkeypatch the clock without touching `datetime.now` globally."""

    return datetime.now(tz=timezone.utc)


class TimerVerdict(str, Enum):
    OK = "ok"
    QUESTION_EXPIRED = "question_expired"
    QUIZ_EXPIRED = "quiz_expired"


@dataclass(frozen=True, slots=True)
class TimerOutcome:
    """The result of evaluating `(session, now)` against the timer budgets."""

    verdict: TimerVerdict
    quiz_elapsed_seconds: int
    question_elapsed_seconds: int


def evaluate_timers(
    session: SessionDoc,
    *,
    now: datetime | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> TimerOutcome:
    """Classify the session against its server-side timer budgets.

    Order of checks matters: a per-quiz expiry supersedes a per-question
    one — the caller flips to `Expired` and auto-grades the remainder
    rather than just the current slot. The tool layer must honour that
    ordering.
    """

    now_dt = now or clock()
    started_at = _aware(session.started_at)
    question_started_at = _aware(session.question_started_at)

    quiz_elapsed = max(0, int((now_dt - started_at).total_seconds()))
    question_elapsed = max(0, int((now_dt - question_started_at).total_seconds()))

    if quiz_elapsed > session.time_limit_seconds:
        return TimerOutcome(
            verdict=TimerVerdict.QUIZ_EXPIRED,
            quiz_elapsed_seconds=quiz_elapsed,
            question_elapsed_seconds=question_elapsed,
        )

    per_question_limit = session.per_question_limit_seconds
    if per_question_limit and question_elapsed > per_question_limit:
        return TimerOutcome(
            verdict=TimerVerdict.QUESTION_EXPIRED,
            quiz_elapsed_seconds=quiz_elapsed,
            question_elapsed_seconds=question_elapsed,
        )

    return TimerOutcome(
        verdict=TimerVerdict.OK,
        quiz_elapsed_seconds=quiz_elapsed,
        question_elapsed_seconds=question_elapsed,
    )


def _aware(dt: datetime) -> datetime:
    # Cosmos round-trips datetimes through ISO 8601 + UTC; if a caller
    # synthesised a naive datetime, normalise here so the arithmetic
    # never mixes naive + aware (which would raise `TypeError`).
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


__all__ = ["TimerOutcome", "TimerVerdict", "evaluate_timers", "utc_now"]
