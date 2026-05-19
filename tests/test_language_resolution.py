"""Language resolution pipeline (TASK-163).

Asserts the end-to-end pipeline: detect → persist → propagate →
coverage-consent fallback.

  * `detect_language` produces high-confidence guesses for FR / EN / ES.
  * `set_language` persists the user's preferred language (FR-010).
  * `start_quiz` reads the persisted language and propagates it to AI
    Search filters (FR-005).
  * Coverage gap → `E_NO_COVERAGE` with `suggested_fallback` —
    **never** a silent language switch (GOV-025).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.language_detection import detect_language
from src.agent.tools import ToolDeps, build_tools
from src.data.models import TopicDoc
from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
from tests.integration.conftest import FakeCosmosRepository

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Bonjour, je voudrais commencer un quiz sur Azure", "fr"),
        ("Hola, quiero empezar un cuestionario sobre Azure en español", "es"),
        ("Hi, please start an English quiz on Azure", "en"),
    ],
)
def test_detect_language_identifies_first_message(utterance: str, expected: str) -> None:
    guess = detect_language(utterance)
    assert guess.code == expected


@pytest.mark.asyncio
async def test_set_language_persists_preference() -> None:
    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    tools = build_tools(deps)
    principal = Principal(entra_oid="user-1")
    result = await tools["set_language"](
        {"user_id": "user-1", "language": "fr"}, principal
    )
    assert result.ok and result.data["language"] == "fr"
    stored_user = await repo.get_user("user-1")
    assert stored_user is not None and stored_user.language == "fr"


@pytest.mark.asyncio
async def test_start_quiz_propagates_language_to_search_filter() -> None:
    repo = FakeCosmosRepository()
    search = build_fake_search(count=3, language="es")
    deps = ToolDeps(
        repo=repo,
        search=search,  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    topic = TopicDoc(
        id="azure-networking",
        topic_id="azure-networking",
        labels={"en": "Networking", "fr": "Réseau", "es": "Redes"},
        counts={"en": 0, "fr": 0, "es": 5},
        default_language="es",
        enabled=True,
        updated_at=NOW,
    )
    payload = topic.model_dump(by_alias=True, exclude_none=True, mode="json")
    await repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]

    tools = build_tools(deps)
    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": "es",
            "channel": "text",
        },
        Principal(entra_oid="user-1"),
    )
    assert result.ok and result.data["language"] == "es"
    assert result.data["question"]["question_id"].endswith("-es")


@pytest.mark.asyncio
async def test_coverage_gap_returns_e_no_coverage_without_silent_switch() -> None:
    """GOV-025: when coverage is zero in the requested language, the tool
    returns `E_NO_COVERAGE` with a `suggested_fallback`. The agent is
    responsible for the consent flow — the tool itself NEVER switches."""

    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    topic = TopicDoc(
        id="azure-networking",
        topic_id="azure-networking",
        labels={"en": "Networking"},
        counts={"en": 5, "fr": 0, "es": 5},
        default_language="en",
        enabled=True,
        updated_at=NOW,
    )
    payload = topic.model_dump(by_alias=True, exclude_none=True, mode="json")
    await repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]

    tools = build_tools(deps)
    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": "fr",
            "channel": "text",
        },
        Principal(entra_oid="user-1"),
    )
    assert result.ok is False
    assert result.error["code"] == "E_NO_COVERAGE"
    assert result.error["detail"]["suggested_fallback"] in {"en", "es"}
