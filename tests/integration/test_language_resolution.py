"""TASK-064 / FR-010, FR-011, FR-014 — first-message language detection.

Two surfaces, two tests:

  1. The cheap server-side classifier (`detect_language`) maps an
     unambiguous French / Spanish / English first message to the right
     code, and returns `None` on ambiguous input. This is the
     belt-and-braces guard; the load-bearing path is the model itself
     calling `set_language(user_id, "fr")` from the contract layer's
     instructions.

  2. The dispatcher accepts a model-issued `set_language` call with a
     valid ISO 639-1 code. (Tool-body validation against SEC-010 lives
     in 005-tools; here we exercise that the dispatcher routes the call
     through without rejecting it.)

We deliberately do NOT test the model side here — that's an end-to-end
test against a live Foundry endpoint and is gated behind TEST-003..005
in 009-testing.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agent.dispatcher import ALLOWED_TOOLS, Dispatcher, Principal, ToolResult
from src.agent.language_detection import detect_language


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Bonjour, je voudrais commencer un quiz sur Azure en français", "fr"),
        ("Hola, quiero empezar un cuestionario sobre Azure en español", "es"),
        ("Hi, please start a quiz about Azure networking in English", "en"),
        ("Merci, c'est très bien — on commence ?", "fr"),
        ("¿Qué temas tenéis sobre Azure?", "es"),
    ],
)
def test_unambiguous_utterances_resolve(utterance: str, expected: str) -> None:
    guess = detect_language(utterance)
    assert guess.code == expected, (
        f"expected {expected!r}, got {guess.code!r}; scores={guess.scores}"
    )
    assert guess.confidence > 0.5


@pytest.mark.parametrize(
    "utterance",
    [
        "",  # empty
        "ok",  # too short
        "azure 123",  # no language markers
        "the start le commencer",  # mixed equal English + French markers — should not pick
    ],
)
def test_ambiguous_utterances_return_none(utterance: str) -> None:
    guess = detect_language(utterance)
    assert guess.code is None
    assert guess.confidence == 0.0


@pytest.mark.asyncio
async def test_dispatcher_routes_set_language_call() -> None:
    invocations: list[dict[str, Any]] = []

    async def set_language_body(args, principal: Principal) -> ToolResult:
        invocations.append(dict(args))
        return ToolResult(ok=True, data={"language": args["language"]})

    async def passthrough(args, principal):
        return ToolResult(ok=True, data={})

    tools: dict[str, Any] = {name: passthrough for name in ALLOWED_TOOLS}
    tools["set_language"] = set_language_body

    class _Store:
        async def get_session(self, *_a, **_kw):  # pragma: no cover - not called
            raise AssertionError("set_language must not consult the session store")

        async def pause_session(self, *_a, **_kw):  # pragma: no cover - not called
            raise AssertionError("set_language must not pause sessions")

    dispatcher = Dispatcher(
        tools=tools,
        session_store=_Store(),
        frame_provider=lambda _s: None,  # type: ignore[arg-type]
    )

    result = await dispatcher.dispatch(
        "set_language",
        {"user_id": "user-1", "language": "fr"},
        Principal(entra_oid="user-1"),
    )
    assert result.ok is True
    assert invocations == [{"user_id": "user-1", "language": "fr"}]
