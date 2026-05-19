"""TASK-063 / GOV-010 — agent registers exactly the five allowed tools.

This is the load-bearing guard against scope creep. If a contributor
quietly adds a sixth tool somewhere — to the dispatcher, to the agent
factory's tool-descriptor list, or to the allowlist itself — this test
fails the build.

Three assertions:

  1. `ALLOWED_TOOLS` is exactly the documented five names (no more, no
     fewer, no typos).
  2. The agent factory's tool-descriptor builder produces a descriptor
     for every allowed tool, and refuses to build a descriptor for any
     other name (defence-in-depth on top of the frozenset).
  3. The dispatcher refuses to instantiate with extra tools.
"""

from __future__ import annotations

import pytest

from src.agent.dispatcher import ALLOWED_TOOLS, Dispatcher
from src.agent.quiz_agent import _tool_descriptor
from src.common.exceptions import FlintConfigurationError, FlintValidationError


EXPECTED_TOOLS: frozenset[str] = frozenset(
    {"list_topics", "set_language", "start_quiz", "submit_answer", "get_results"}
)


def test_allowlist_is_exactly_the_documented_five() -> None:
    assert ALLOWED_TOOLS == EXPECTED_TOOLS, (
        f"agent allowlist drift: extra={sorted(ALLOWED_TOOLS - EXPECTED_TOOLS)}, "
        f"missing={sorted(EXPECTED_TOOLS - ALLOWED_TOOLS)}"
    )
    assert len(ALLOWED_TOOLS) == 5


def test_descriptor_built_for_every_allowed_tool() -> None:
    descriptors = [_tool_descriptor(name) for name in sorted(ALLOWED_TOOLS)]
    assert {d["name"] for d in descriptors} == EXPECTED_TOOLS
    for d in descriptors:
        assert d["type"] == "function"
        assert d["description"], f"tool {d['name']!r} missing description"


def test_descriptor_rejects_unknown_tool_name() -> None:
    with pytest.raises(FlintConfigurationError):
        _tool_descriptor("steal_answer_key")


def test_dispatcher_refuses_tools_outside_allowlist() -> None:
    async def _stub(_args, _principal):  # pragma: no cover - never called
        raise AssertionError("stub tool body must not run during construction")

    tools = {name: _stub for name in ALLOWED_TOOLS}
    tools["evil_extra"] = _stub
    with pytest.raises(FlintValidationError):
        Dispatcher(
            tools=tools,
            session_store=_NullSessionStore(),
            frame_provider=lambda _s: None,  # type: ignore[arg-type]
        )


def test_dispatcher_refuses_missing_tools() -> None:
    async def _stub(_args, _principal):  # pragma: no cover - never called
        raise AssertionError("stub tool body must not run during construction")

    partial = {name: _stub for name in list(ALLOWED_TOOLS)[:3]}
    with pytest.raises(FlintValidationError):
        Dispatcher(
            tools=partial,
            session_store=_NullSessionStore(),
            frame_provider=lambda _s: None,  # type: ignore[arg-type]
        )


class _NullSessionStore:
    async def get_session(self, session_id, user_id):  # pragma: no cover - never called
        raise AssertionError("session store must not be touched at construction time")

    async def pause_session(self, session):  # pragma: no cover - never called
        raise AssertionError("session store must not be touched at construction time")
