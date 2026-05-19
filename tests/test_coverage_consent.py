"""Coverage-fallback consent flow (TEST-022 / TASK-180 / GOV-024 / GOV-025).

The two-turn consent dance:

  1. ``start_quiz(lang=requested)`` returns ``E_NO_COVERAGE`` with
     ``suggested_fallback`` (or `null`).
  2. Affirmative consent →
     ``set_language(suggested_fallback)`` → retry ``start_quiz``.
  3. Negative consent → ``list_topics(language=requested)`` to offer a
     different topic.

The load-bearing assertion: ``set_language`` is invoked **between**
the two ``start_quiz`` calls. A silent cross-language serve would
skip the middle call entirely.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.data.models import TopicDoc
from tests.integration._tools_fakes import RecordingEmitter, build_fake_search
from tests.integration.conftest import FakeCosmosRepository

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="user-1")


async def _deps_with_topic():
    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=5, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    topic = TopicDoc(
        id="azure-networking",
        topic_id="azure-networking",
        labels={"en": "Networking", "fr": "Réseau", "es": "Redes"},
        counts={"en": 5, "fr": 0, "es": 0},
        default_language="en",
        enabled=True,
        updated_at=NOW,
    )
    payload = topic.model_dump(by_alias=True, exclude_none=True, mode="json")
    await repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]
    return deps


@pytest.mark.asyncio
async def test_affirmative_consent_calls_set_language_then_retries() -> None:
    """**TEST-022 load-bearing assertion**: `set_language` lands between
    the two `start_quiz` calls on the consent path."""

    deps = await _deps_with_topic()
    tools = build_tools(deps)
    trace: list[str] = []

    async def trace_call(name: str, args: dict[str, object]):
        trace.append(name)
        return await tools[name](args, PRINCIPAL)

    first = await trace_call(
        "start_quiz",
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": "fr",
            "channel": "text",
        },
    )
    assert first.ok is False and first.error["code"] == "E_NO_COVERAGE"
    suggested = first.error["detail"]["suggested_fallback"]
    assert suggested == "en"

    set_result = await trace_call(
        "set_language", {"user_id": "user-1", "language": suggested}
    )
    assert set_result.ok

    retry = await trace_call(
        "start_quiz",
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": suggested,
            "channel": "text",
        },
    )
    assert retry.ok
    assert trace == ["start_quiz", "set_language", "start_quiz"]


@pytest.mark.asyncio
async def test_negative_consent_does_not_call_set_language() -> None:
    """Negative consent path: agent offers a different topic via
    `list_topics(language=requested)` and the user's original language
    preference is preserved."""

    deps = await _deps_with_topic()
    tools = build_tools(deps)
    first = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": "fr",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert first.error["code"] == "E_NO_COVERAGE"

    # Negative consent — agent lists in the REQUESTED language. No
    # `set_language` invocation; the user's preference is preserved.
    list_result = await tools["list_topics"](
        {"language": "fr", "user_id": "user-1"}, PRINCIPAL
    )
    assert list_result.ok


@pytest.mark.asyncio
async def test_code_switched_utterance_does_not_flip_session_language() -> None:
    """A brief code-switched utterance ("the answer is la primera") inside
    an `en` session does NOT call `set_language`. Language only changes
    via an explicit user request (GOV-027)."""

    deps = await _deps_with_topic()
    tools = build_tools(deps)
    # Set the session up in `en` and send a Spanish-flavoured utterance
    # to submit_answer. The session-language pin is preserved.
    from tests.integration.conftest import make_session_doc

    session = make_session_doc(n=3).model_copy(
        update={
            "language": "en",
            "requested_language": "en",
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    stored = await deps.repo.create_session(session)
    result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "the answer is la primera",
            "channel": "text",
        },
        PRINCIPAL,
    )
    # Whatever the verdict, the session's language is unchanged.
    refreshed = await deps.repo.get_session(stored.id, "user-1")
    assert refreshed.language == "en"
