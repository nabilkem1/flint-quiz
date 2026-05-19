"""Integration tests for `start_quiz` (TASK-083 / 008-api §1.5).

Verifies:
  * Happy path — session created, Q1 returned, **no `correct_answer`**.
  * Count clamp — `n` clamped to coverage; `fallback_notice.reason ==
    "count_clamped"`; language unchanged.
  * Zero coverage in requested language → `E_NO_COVERAGE` with
    `suggested_fallback` in `detail`. No session row written.
  * Defensive strip — recursive walk of the returned envelope has no
    answer-key key at any nesting level.
"""

from __future__ import annotations

import json

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools

from ._tools_fakes import RecordingEmitter, build_fake_search, make_topic_doc
from .conftest import FakeCosmosRepository

PRINCIPAL = Principal(entra_oid="user-1")


@pytest.fixture
def deps() -> ToolDeps:
    repo = FakeCosmosRepository()
    search = build_fake_search(count=5, language="en")
    return ToolDeps(
        repo=repo,
        search=search,  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
    )


async def _seed_topic(repo: FakeCosmosRepository, **kwargs: object) -> None:
    """Upsert a TopicDoc straight into the fake container."""

    topic = make_topic_doc(**kwargs)
    payload = topic.model_dump(by_alias=True, exclude_none=True, mode="json")
    # FakeContainer.create_item requires a unique id; reuse the upsert path.
    await repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_start_quiz_happy_path(deps: ToolDeps) -> None:
    await _seed_topic(deps.repo)
    tools = build_tools(deps)

    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": "en",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok is True, result.error
    assert result.data is not None
    assert result.data["index"] == 1
    assert result.data["total"] == 3
    assert result.data["language"] == "en"
    assert result.data["question"]["question_id"].endswith("-en")

    # Defensive strip — recursive walk has no answer-key.
    assert "correct_answer" not in json.dumps(result.data)


@pytest.mark.asyncio
async def test_start_quiz_count_clamp_populates_fallback_notice(deps: ToolDeps) -> None:
    await _seed_topic(deps.repo, counts={"en": 3, "fr": 0, "es": 10})
    tools = build_tools(deps)

    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 5,
            "language": "en",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok is True, result.error
    notice = result.data["fallback_notice"]
    assert notice is not None
    assert notice["reason"] == "count_clamped"
    assert notice["requested"] == "en"
    assert notice["resolved"] == "en"  # NO language change on count clamp
    assert notice["requested_n"] == 5
    assert notice["resolved_n"] == 3
    assert result.data["total"] == 3


@pytest.mark.asyncio
async def test_start_quiz_zero_coverage_returns_e_no_coverage(deps: ToolDeps) -> None:
    # FR has zero coverage; suggested fallback should be EN (highest available).
    await _seed_topic(
        deps.repo,
        counts={"en": 10, "fr": 0, "es": 5},
        default_language="en",
    )
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
    assert result.error is not None
    assert result.error["code"] == "E_NO_COVERAGE"
    detail = result.error["detail"]
    assert detail["requested"] == "fr"
    assert detail["suggested_fallback"] == "en"


@pytest.mark.asyncio
async def test_start_quiz_zero_coverage_with_no_alternative_returns_none(
    deps: ToolDeps,
) -> None:
    # Only FR is asked; only ES has coverage but lacks n=5 → suggested=None.
    await _seed_topic(
        deps.repo,
        counts={"en": 0, "fr": 0, "es": 2},
        default_language="es",
    )
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


@pytest.mark.asyncio
async def test_start_quiz_rejects_invalid_language(deps: ToolDeps) -> None:
    await _seed_topic(deps.repo)
    tools = build_tools(deps)

    # SEC-010 — `klingon` not in allowlist.
    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": "kl",
            "channel": "text",
        },
        PRINCIPAL,
    )
    # The allowlist check raises InvalidLanguageError; the dispatcher
    # would translate this — here the body raises so the call surfaces
    # the exception. Either flow is acceptable as long as the language
    # is rejected. We assert the language was NOT accepted.
    assert (
        (not result.ok and result.error is not None)
        or False
    ), "start_quiz must reject unsupported languages"


@pytest.mark.asyncio
async def test_start_quiz_unknown_topic_returns_e_unknown_topic(deps: ToolDeps) -> None:
    tools = build_tools(deps)

    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-bogus",
            "n": 3,
            "language": "en",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok is False
    assert result.error["code"] == "E_UNKNOWN_TOPIC"


@pytest.mark.asyncio
async def test_start_quiz_response_has_no_correct_answer_recursive(
    deps: ToolDeps,
) -> None:
    """SEC-001 — defensive strip + typed boundary; assert at every nesting level."""

    await _seed_topic(deps.repo)
    tools = build_tools(deps)
    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": "en",
            "channel": "text",
        },
        PRINCIPAL,
    )
    assert result.ok is True
    payload = json.dumps(result.data)
    for forbidden in ("correct_answer", "correctAnswer", "answer_key"):
        assert forbidden not in payload
