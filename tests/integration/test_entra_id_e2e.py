"""Entra ID end-to-end test (TASK-127 / SEC-003).

Asserts the load-bearing contract: the agent surface honours the Entra
OID claim as the source of truth for `user_id`, and ANY mismatch
between the `user_id` arg and the authenticated principal is rejected.
Anonymous traffic — modelled here as a Principal with an empty `oid`
— is rejected on every tool that accepts a `user_id`.

The Hosted Agent's bearer-token validation is owned by Foundry +
APIM; this test exercises the **server-side** half: even if an
attacker bypassed those layers and reached the dispatcher with
mismatched args, the dispatcher rejects.
"""

from __future__ import annotations

import pytest

from src.agent.dispatcher import ALLOWED_TOOLS, Dispatcher, Principal, ToolResult
from src.agent.tools import ToolDeps, build_tools

from ._tools_fakes import RecordingEmitter, build_fake_search

PRINCIPAL_ALICE = Principal(entra_oid="alice-oid")
PRINCIPAL_BOB = Principal(entra_oid="bob-oid")
PRINCIPAL_ANONYMOUS = Principal(entra_oid="")


@pytest.fixture
def deps():
    from .conftest import FakeCosmosRepository

    return ToolDeps(
        repo=FakeCosmosRepository(),
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
    )


# ---------------------------------------------------------------------------
# Dispatcher-level enforcement (008-api §1.5.7 / 008-api §1.6 / SEC-003)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_rejects_user_id_mismatch_on_set_language(deps) -> None:
    """`user_id` in args MUST equal the authenticated principal.

    This is the load-bearing tool-arg impersonation defence the
    dispatcher enforces before any tool body runs.
    """

    async def passthrough(args, principal):
        return ToolResult(ok=True, data={})

    tools = {name: passthrough for name in ALLOWED_TOOLS}

    class _Store:
        async def get_session(self, *_a, **_k):  # pragma: no cover
            raise AssertionError("auth-mismatch path must not consult the store")

        async def pause_session(self, *_a, **_k):  # pragma: no cover
            raise AssertionError("auth-mismatch path must not pause sessions")

    dispatcher = Dispatcher(
        tools=tools,
        session_store=_Store(),
        frame_provider=lambda _s: None,  # type: ignore[arg-type]
    )
    result = await dispatcher.dispatch(
        "set_language",
        {"user_id": "carol-oid", "language": "en"},
        PRINCIPAL_ALICE,
    )
    assert result.ok is False
    assert result.error["code"] == "E_AUTH_MISMATCH"


@pytest.mark.asyncio
async def test_anonymous_principal_is_treated_as_mismatch(deps) -> None:
    """Anonymous (empty OID) traffic on a user-bound tool is rejected.

    The dispatcher does not know the difference between "anonymous" and
    "claims-missing-oid"; both surface as a missing/mismatched
    principal. The contract is identical: refuse.
    """

    async def passthrough(args, principal):  # pragma: no cover - never called
        raise AssertionError("anonymous traffic must not reach tool bodies")

    tools = {name: passthrough for name in ALLOWED_TOOLS}

    class _Store:
        async def get_session(self, *_a, **_k):  # pragma: no cover
            raise AssertionError("must not consult store")

        async def pause_session(self, *_a, **_k):  # pragma: no cover
            raise AssertionError("must not pause sessions")

    dispatcher = Dispatcher(
        tools=tools,
        session_store=_Store(),
        frame_provider=lambda _s: None,  # type: ignore[arg-type]
    )
    result = await dispatcher.dispatch(
        "set_language",
        {"user_id": "alice-oid", "language": "en"},
        PRINCIPAL_ANONYMOUS,
    )
    assert result.ok is False
    assert result.error["code"] == "E_AUTH_MISMATCH"


# ---------------------------------------------------------------------------
# Tool-body enforcement (defense in depth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_language_raises_on_principal_mismatch(deps) -> None:
    """Even if the dispatcher's check were bypassed, the tool body
    raises `FlintAuthorizationError` on mismatch."""

    from src.common.exceptions import FlintAuthorizationError

    tools = build_tools(deps)
    with pytest.raises(FlintAuthorizationError):
        await tools["set_language"](
            {"user_id": "carol-oid", "language": "en"},
            PRINCIPAL_ALICE,
        )


@pytest.mark.asyncio
async def test_get_results_raises_on_principal_mismatch(deps) -> None:
    from src.common.exceptions import FlintAuthorizationError

    tools = build_tools(deps)
    with pytest.raises(FlintAuthorizationError):
        await tools["get_results"](
            {"session_id": "s-1", "user_id": "carol-oid"},
            PRINCIPAL_ALICE,
        )


# ---------------------------------------------------------------------------
# Cosmos row keying — userId equals Entra OID end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_row_user_id_equals_principal_oid(deps) -> None:
    """`start_quiz` writes the session with userId == principal OID.

    A reviewer reading the resulting Cosmos row should be able to
    verify the session belongs to a specific Entra principal — that's
    the SEC-003 / SEC-009 boundary made auditable.
    """

    from src.data.models import TopicDoc
    from datetime import datetime, timezone as _tz

    topic = TopicDoc(
        id="azure-networking",
        topic_id="azure-networking",
        labels={"en": "Networking"},
        counts={"en": 5},
        default_language="en",
        enabled=True,
        updated_at=datetime.now(tz=_tz.utc),
    )
    payload = topic.model_dump(by_alias=True, exclude_none=True, mode="json")
    await deps.repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]

    tools = build_tools(deps)
    result = await tools["start_quiz"](
        {
            "user_id": "alice-oid",
            "topic": "azure-networking",
            "n": 3,
            "language": "en",
            "channel": "text",
        },
        PRINCIPAL_ALICE,
    )
    assert result.ok is True
    session_id = result.data["session_id"]
    refreshed = await deps.repo.get_session(session_id, "alice-oid")
    assert refreshed.user_id == "alice-oid"
