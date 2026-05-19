"""Shared fakes for the 005-tools integration tests.

Two in-memory implementations:

  * :class:`FakeQuestionSearch` — duck-types the subset of
    :class:`src.data.question_search.QuestionSearch` the tool layer
    touches (``search_topic``, ``get_question_view``, ``get_answer_key``).
    Backed by a dict of authored seed records.

  * :class:`RecordingEmitter` — captures `grading_event` emissions so
    tests can assert the at-most-once-per-persisted-answer property
    (TEST-007 / TEST-010).

These live alongside the existing ``FakeCosmosRepository`` from
``conftest.py`` — the two together cover every dependency the tool
layer needs without booting the emulator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.data.models import (
    AnswerKey,
    Difficulty,
    LanguageCode,
    LogicalId,
    Option,
    QuestionId,
    QuestionView,
    TopicDoc,
)


@dataclass
class _FakeRecord:
    """Authored question row + answer key (in-memory)."""

    question_id: str
    logical_id: str
    topic: str
    language: str
    text: str
    options: list[Option]
    correct_answer: tuple[str, ...]
    difficulty: Difficulty
    score_weight: float = 1.0
    explanation: str | None = None


class FakeQuestionSearch:
    """Duck-typed `QuestionSearch` backed by a dict of records.

    Tests construct it via :func:`build_fake_search` with a small seed
    corpus. Cross-language contamination is impossible — the lookup
    is keyed on the per-language `question_id` (e.g., `az-net-vpn-001-fr`).
    """

    def __init__(self, records: list[_FakeRecord]) -> None:
        self._records: dict[str, _FakeRecord] = {r.question_id: r for r in records}

    async def search_topic(
        self,
        topic: str,
        language: LanguageCode | str,
        difficulty: Difficulty | None = None,
        top: int = 200,
    ) -> list[LogicalId]:
        out: list[LogicalId] = []
        for r in self._records.values():
            if r.topic != topic or r.language != str(language):
                continue
            if difficulty is not None and r.difficulty != difficulty:
                continue
            out.append(LogicalId(r.question_id))
        return out[:top]

    async def get_question_view(self, question_id: QuestionId | str) -> QuestionView:
        r = self._records[str(question_id)]
        return QuestionView(
            question_id=r.question_id,
            text=r.text,
            options=list(r.options),
            difficulty=r.difficulty,
        )

    async def get_answer_key(self, question_id: QuestionId | str) -> AnswerKey:
        r = self._records[str(question_id)]
        return AnswerKey(
            question_id=r.question_id,
            correct=frozenset(r.correct_answer),
            score_weight=r.score_weight,
        )


@dataclass
class RecordingEmitter:
    """Capture `emit(name, properties)` calls for assertions."""

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, name: str, properties: Mapping[str, Any]) -> None:
        self.events.append((name, dict(properties)))

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.events if n == name)


def build_fake_search(
    *,
    topic: str = "azure-networking",
    language: str = "en",
    count: int = 5,
    multi_correct_index: int | None = None,
) -> FakeQuestionSearch:
    """Build a synthetic question bank for a single (topic, language).

    `multi_correct_index` (when set) marks one record as multi-correct
    (`{A, C}`) so the grader's set-equality branch is exercised.
    """

    records: list[_FakeRecord] = []
    for i in range(count):
        qid = f"{topic}-{i:03d}-{language}"
        is_multi = multi_correct_index is not None and i == multi_correct_index
        records.append(
            _FakeRecord(
                question_id=qid,
                logical_id=f"{topic}-{i:03d}",
                topic=topic,
                language=language,
                text=f"Question {i + 1} about {topic} in {language}?",
                options=[
                    Option(key="A", text="Option A"),
                    Option(key="B", text="Option B"),
                    Option(key="C", text="Option C"),
                    Option(key="D", text="Option D"),
                ],
                correct_answer=("A", "C") if is_multi else ("B",),
                difficulty="medium",
                score_weight=1.0,
            )
        )
    return FakeQuestionSearch(records)


def make_topic_doc(
    *,
    topic_id: str = "azure-networking",
    counts: dict[str, int] | None = None,
    default_language: str = "en",
) -> TopicDoc:
    counts = counts or {"en": 10, "fr": 10, "es": 10}
    return TopicDoc(
        id=topic_id,
        topic_id=topic_id,
        labels={"en": "Azure Networking", "fr": "Réseau Azure", "es": "Redes Azure"},
        counts=counts,
        default_language=default_language,
        enabled=True,
        updated_at=datetime.now(tz=timezone.utc),
    )
