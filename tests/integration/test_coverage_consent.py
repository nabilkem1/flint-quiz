"""Coverage-fallback consent flow (TASK-189 / TEST-022 / GOV-025).

The agent flow is two turns:

  1. `start_quiz` returns `E_NO_COVERAGE` with `suggested_fallback` in
     `detail`. The tool layer **never** auto-switches the language.
  2. The agent reads the gap to the user in the *requested* (active)
     language. On affirmative consent the agent calls `set_language`
     and re-invokes `start_quiz` with the new language. Negative consent
     routes the agent to `list_topics(language=requested)` to offer a
     different topic.

These tests simulate the agent's tool-call sequence with the real tool
implementations behind a fake repo + search. The critical invariant
asserted by TEST-022: ``set_language`` is invoked **between** the two
``start_quiz`` calls on the affirmative path. No silent cross-language
serve is possible.
"""

from __future__ import annotations

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools

from ._tools_fakes import RecordingEmitter, build_fake_search, make_topic_doc
from .conftest import FakeCosmosRepository

PRINCIPAL = Principal(entra_oid="user-1")


@pytest.fixture
def deps() -> ToolDeps:
    # FR has 0 coverage; ES has plenty. The fake search is seeded with EN
    # questions so the fallback retry succeeds.
    return ToolDeps(
        repo=FakeCosmosRepository(),
        search=build_fake_search(count=5, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
    )


async def _seed_topic(deps: ToolDeps) -> None:
    topic = make_topic_doc(
        counts={"en": 10, "fr": 0, "es": 5}, default_language="en"
    )
    payload = topic.model_dump(by_alias=True, exclude_none=True, mode="json")
    await deps.repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_consent_affirmative_calls_set_language_then_retries(
    deps: ToolDeps,
) -> None:
    """Simulated agent flow:

      1. `start_quiz(lang=fr)` → `E_NO_COVERAGE` with suggested='en'.
      2. user says "yes" → agent calls `set_language(en)`.
      3. agent re-calls `start_quiz(lang=en)` → success.

    Trace the order of tool invocations and assert `set_language` lands
    between the two `start_quiz` calls.
    """

    await _seed_topic(deps)
    tools = build_tools(deps)
    trace: list[str] = []

    async def trace_call(name: str, args: dict[str, object]):
        trace.append(name)
        return await tools[name](args, PRINCIPAL)

    # Turn 1 — user asks in French.
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
    assert first.ok is False
    assert first.error["code"] == "E_NO_COVERAGE"
    suggested = first.error["detail"]["suggested_fallback"]
    assert suggested == "en"

    # Turn 2a — agent honours consent by calling `set_language`.
    set_result = await trace_call(
        "set_language", {"user_id": "user-1", "language": suggested}
    )
    assert set_result.ok is True

    # Turn 2b — agent re-issues start_quiz with the new language.
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
    assert retry.ok is True

    # **The TEST-022 assertion** — set_language is sandwiched between the
    # two start_quiz calls on the consent path.
    assert trace == ["start_quiz", "set_language", "start_quiz"]


@pytest.mark.asyncio
async def test_consent_negative_offers_a_different_topic(deps: ToolDeps) -> None:
    """Negative consent — the agent does NOT call `set_language`; it calls
    `list_topics(language=requested)` to offer a different topic."""

    await _seed_topic(deps)
    tools = build_tools(deps)
    trace: list[str] = []

    # Turn 1.
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
    trace.append("start_quiz")
    assert first.ok is False
    assert first.error["code"] == "E_NO_COVERAGE"

    # Negative path — agent lists topics in the user's REQUESTED language.
    list_result = await tools["list_topics"](
        {"language": "fr", "user_id": "user-1"}, PRINCIPAL
    )
    trace.append("list_topics")
    assert list_result.ok is True

    # Critical: no `set_language` call between the two — the original
    # language preference is preserved.
    assert "set_language" not in trace


@pytest.mark.asyncio
async def test_zero_coverage_anywhere_returns_null_suggested(deps: ToolDeps) -> None:
    """If no language has `count >= n`, `suggested_fallback` is null and the
    agent must offer a different topic — there is no language switch
    possible at all."""

    # Only ES has 1 entry; user asks for n=5 in FR.
    topic_payload = make_topic_doc(
        counts={"en": 1, "fr": 0, "es": 1}, default_language="en"
    ).model_dump(by_alias=True, exclude_none=True, mode="json")
    await deps.repo._topics.upsert_item(body=topic_payload)  # type: ignore[attr-defined]

    tools = build_tools(deps)
    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 5,
            "language": "fr",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok is False
    assert result.error["code"] == "E_NO_COVERAGE"
    assert result.error["detail"]["suggested_fallback"] is None
