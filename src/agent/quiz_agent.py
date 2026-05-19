"""Single MAF agent factory (TASK-061 / 063 / 065 / 069 / 072).

`create_agent()` is the factory the Hosted Agent runtime instantiates.
It wires four things together:

  1. **AppConfig-sourced model deployment name.** Reading it at
     construction (never hard-coded) lets us flip the model in
     production by editing one row, without a redeploy (GOV-150 /
     GOV-160). The local-dev shim reads the same key, so dev and prod
     resolve from the same source of truth.
  2. **Exactly five registered tools** (`ALLOWED_TOOLS`). A sixth tool
     is impossible to register — the dispatcher refuses at construction
     time and the registration helper here triple-checks against the
     same frozen constant.
  3. **The dispatcher** (TASK-070). Every MAF tool-call request is
     routed through `Dispatcher.dispatch`, which enforces the allowlist,
     the prompt-hash check, the per-session mutex, and the audit-grade
     spans.
  4. **Per-turn output cap of 600 tokens** (TASK-072 / GOV-091). The
     factory configures the MAF runtime so the runtime — not the model
     — guarantees the budget; truncation events emit at the runtime
     layer, not from the prompt.

The factory is intentionally small. Tool **bodies** live in 005-tools;
this module imports them lazily through a registry callable so the
import-linter contract in `pyproject.toml` still passes (the dispatcher
remains the only direct importer of tool functions). For the 004
baseline we accept the registry as a parameter so tests do not need
the real bodies.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.agent.dispatcher import (
    ALLOWED_TOOLS,
    Dispatcher,
    EventEmitter,
    Principal,
    SessionStore,
    ToolCallable,
    ToolResult,
)
from src.agent.prompts.compose import SessionFrame, verify_manifest
from src.common.exceptions import FlintConfigurationError
from src.data.models import SessionDoc

logger = logging.getLogger(__name__)

# Per-turn output cap (GOV-091 / TASK-072). The MAF runtime enforces
# this; `agent.output_truncated` is emitted by the runtime, not by us.
# Phrasing blocks are budgeted well under 200 tokens for most turns.
OUTPUT_TOKEN_CAP: int = 600


class AppConfig(Protocol):
    """Subset of Azure App Configuration we touch.

    The production wiring binds this to a small wrapper around
    `azure.appconfiguration.aio.AzureAppConfigurationClient`. Tests pass
    a `dict`-backed fake. We keep the API minimal — one async getter,
    one sync getter for the agent-startup hot path.
    """

    def get_required(self, key: str) -> str: ...


class FoundryAgentClient(Protocol):
    """Subset of the Foundry projects client used to spin up the agent.

    Defined as a Protocol so unit tests can substitute a no-op factory.
    The production wiring binds to
    `azure.ai.projects.aio.AIProjectClient.agents`.
    """

    async def create_agent(
        self,
        *,
        model: str,
        name: str,
        instructions: str,
        tools: list[dict[str, Any]],
        max_output_tokens: int,
    ) -> "AgentHandle": ...


@dataclass(frozen=True, slots=True)
class AgentHandle:
    """Opaque handle to the deployed Foundry agent.

    Carries the IDs the channel layer needs to invoke the agent later
    (e.g., when creating a thread). No other state escapes — the channel
    layer goes through MAF SDK calls, not through this module.
    """

    id: str
    model: str


@dataclass(frozen=True, slots=True)
class AgentRegistration:
    """Result of `create_agent`. Returned for observability + handover.

    Includes the dispatcher (so the channel layer can install it as the
    tool-call handler) and the agent handle (for thread creation / deploy
    sanity checks). The tools list is fixed at construction; recording
    it here makes the registration test (`test_tool_registration.py`)
    trivial.
    """

    agent: AgentHandle
    dispatcher: Dispatcher
    tool_names: frozenset[str]


def _tool_descriptor(name: str) -> dict[str, Any]:
    """Build the MAF tool-registration descriptor for a tool name.

    The full JSON schema for each tool lives in 008-api §1; this
    descriptor only carries the name + a short description so the model
    can choose. The dispatcher is the load-bearing validator — schemas
    in the descriptor would be a redundant second source of truth (and
    would drift). Tool argument validation happens at the body layer
    (005-tools) where the Pydantic models live.
    """

    if name not in ALLOWED_TOOLS:
        # Should be unreachable — the caller iterates ALLOWED_TOOLS — but
        # the defensive check is cheap and keeps the registration path
        # honest. (Belt: dispatcher refuses. Braces: this guard.)
        raise FlintConfigurationError(
            f"refusing to build descriptor for tool {name!r} outside the allowlist"
        )
    descriptions = {
        "list_topics": "Return the catalog of available quiz topics with localized labels.",
        "set_language": "Persist the user's preferred quiz language. ISO 639-1 only.",
        "start_quiz": "Create a session, seed the shuffle, and return question 1.",
        "submit_answer": "Submit the user's answer for the current question; grading is server-side.",
        "get_results": "Return the final score breakdown when the quiz is complete.",
    }
    return {
        "type": "function",
        "name": name,
        "description": descriptions[name],
    }


def _frame_provider_factory(default_total: int | None = None):
    """Return a `frame_provider` callable for the Dispatcher.

    Pulled out so callers can override the build for tests. The default
    implementation reads the invariant subset directly from the
    SessionDoc — see the `SessionFrame` docstring for what counts as
    invariant. `default_total` is unused in production (the SessionDoc
    knows its `shuffled_ids`); the test wiring uses it to inject a
    deterministic total when a fixture SessionDoc has empty IDs.
    """

    def _build(session: SessionDoc) -> SessionFrame:
        channel = (
            session.channel.value
            if not isinstance(session.channel, str)
            else session.channel
        )
        total = len(session.shuffled_ids) if session.shuffled_ids else (default_total or 0)
        return SessionFrame(
            session_id=session.id,
            user_id=session.user_id,
            topic=session.topic,
            language=session.language,
            channel_at_start=channel,
            total=total,
            time_limit_seconds=session.time_limit_seconds,
            started_at=session.started_at,
        )

    return _build


async def create_agent(
    *,
    app_config: AppConfig,
    foundry_client: FoundryAgentClient,
    tools: Mapping[str, ToolCallable],
    session_store: SessionStore,
    emitter: EventEmitter | None = None,
    agent_name: str = "flint-quiz",
    base_instructions: str | None = None,
) -> AgentRegistration:
    """Build, register, and return the single Flint Quiz agent.

    Steps:
      1. Verify the prompt MANIFEST (refuse to start on layer-file
         drift). This is a fail-loud-on-boot guard — the same check runs
         at deploy time, but the boot-time recheck catches a "code
         deployed but layer file rolled back" race.
      2. Pull the model deployment name from AppConfig.
      3. Validate the tool set is exactly the allowlist.
      4. Build the dispatcher.
      5. Ask Foundry to register the agent with its tool descriptors,
         `instructions=` set to the static portion of the prompt (the
         per-session prompt is recomposed at session start), and
         `max_output_tokens=600` for the 091 cap.

    The static-portion instructions are deliberately small — just the
    identity layer. The contract layer + per-language phrasing + session
    frame are concatenated by `compose()` at session start and used to
    set the per-session system message via the MAF runtime, not via
    `instructions=`. This keeps the Foundry-side `instructions` field
    stable across sessions (Foundry caches by hash internally).
    """

    verify_manifest()

    try:
        model_deployment = app_config.get_required("agent:modelDeploymentName")
    except KeyError as exc:
        raise FlintConfigurationError(
            "AppConfig is missing required key `agent:modelDeploymentName`"
        ) from exc

    extras = tools.keys() - ALLOWED_TOOLS
    if extras:
        raise FlintConfigurationError(
            f"refusing to register tools outside the allowlist: {sorted(extras)}"
        )
    missing = ALLOWED_TOOLS - tools.keys()
    if missing:
        raise FlintConfigurationError(
            f"required tools missing from registry: {sorted(missing)}"
        )

    dispatcher = Dispatcher(
        tools=tools,
        session_store=session_store,
        frame_provider=_frame_provider_factory(),
        emitter=emitter,
    )

    tool_descriptors = [_tool_descriptor(name) for name in sorted(ALLOWED_TOOLS)]

    # `instructions` is the static, hash-independent identity blurb.
    # Per-session contract + phrasing + frame are applied at session
    # start (the MAF runtime accepts a `system_message` per session;
    # `quiz_agent` callers feed `compose()`'s output there).
    instructions = base_instructions or _default_static_instructions()
    handle = await foundry_client.create_agent(
        model=model_deployment,
        name=agent_name,
        instructions=instructions,
        tools=tool_descriptors,
        max_output_tokens=OUTPUT_TOKEN_CAP,
    )

    return AgentRegistration(
        agent=handle,
        dispatcher=dispatcher,
        tool_names=ALLOWED_TOOLS,
    )


def _default_static_instructions() -> str:
    """Return the agent's hash-independent identity blurb.

    Read at startup — not at every session start — and deliberately
    short. Foundry's `instructions=` field is global across sessions of
    the same agent record; it is NOT the same surface as the per-session
    system message produced by `compose()`. Keep these two surfaces
    distinct so a per-language phrasing change does not require a
    Foundry-side agent re-registration.
    """

    return (
        "You are Flint, a conversational quiz host. You operate inside a single Foundry "
        "Hosted Agent runtime serving both text and voice. Per-session governance "
        "(language, refusal copy, behavioural contract) is applied via the per-session "
        "system message; this static blurb is the identity-only header. You never grade; "
        "the `submit_answer` tool grades and persists. The dispatcher rejects any tool "
        "name outside the five-tool allowlist; do not attempt others."
    )


__all__ = [
    "AgentHandle",
    "AgentRegistration",
    "AppConfig",
    "FoundryAgentClient",
    "OUTPUT_TOKEN_CAP",
    "create_agent",
]
