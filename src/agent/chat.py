"""Interactive chat client — wires the 5 tools to the deployed Foundry agent.

Run with::

    python -m src.agent.chat

The client uses :class:`agent_framework.foundry.FoundryAgent` which:

  * Connects to the registered ``fq-dev-agent`` (or whatever
    ``$AGENT_NAME`` points to).
  * Auto-converts the Python callables we pass via ``tools=[...]`` into
    OpenAI function descriptors.
  * Handles the **tool-dispatch loop** internally — when the model
    returns a tool call, MAF executes the matching Python callable and
    feeds the result back. We never write the polling loop ourselves.

Why this lives in a separate entry point (not ``__main__.py``): the
production container's job is just to register the agent + serve a
liveness probe. The chat client is an operator/developer surface for
interactive testing. A future production "chat-as-a-service" container
would build on this same wiring.

Identity:
    `DefaultAzureCredential` resolves to whatever the host environment
    provides — `az login` locally, or the UAMI in the Container App.
    The agent definition + tool execution both need the same Foundry
    data-plane access (``Cognitive Services OpenAI User`` + the
    custom ``Foundry Agents Writer`` role).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.data.cosmos_repository import CosmosRepository
from src.data.question_search import QuestionSearch, build_search_client

logger = logging.getLogger("flint-quiz.chat")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required env: {name}")
    return value


# ---------------------------------------------------------------------------
# Tool callables for MAF (FoundryAgent picks these up via `tools=[...]`)
# ---------------------------------------------------------------------------
#
# MAF auto-converts each Python callable into an OpenAI function schema
# from its type hints + docstring. The function names must match the
# tool names registered on the agent (``list_topics``, ``set_language``,
# ``start_quiz``, ``submit_answer``, ``get_results``).
#
# Each wrapper delegates to the underlying tool body via the shared
# :class:`ToolDeps` (Cosmos repo + AI Search client + emitter). The
# dispatcher's prompt-hash + auth checks DON'T run here because we're
# already inside the agent process — the dispatcher's load-bearing
# checks are for cross-process traffic. The tool body's own typed
# Pydantic request models still validate every call.

# Module-level deps holder — initialised once in `main()`.
_DEPS: ToolDeps | None = None
_PRINCIPAL: Principal | None = None
_TOOL_FNS: dict[str, Any] = {}


def _ensure_initialised() -> tuple[ToolDeps, Principal]:
    if _DEPS is None or _PRINCIPAL is None:
        raise RuntimeError("chat.py tools called before main() initialised deps")
    return _DEPS, _PRINCIPAL


async def list_topics(language: str) -> dict[str, Any]:
    """Return the catalog of available quiz topics with localized labels.

    Args:
        language: ISO 639-1 language code (e.g. "en", "fr", "es").
    """

    deps, principal = _ensure_initialised()
    logger.info("tool.list_topics.invoked", extra={"language": language})
    result = await _TOOL_FNS["list_topics"]({"language": language}, principal)
    if not result.ok:
        logger.warning("tool.list_topics.failed", extra={"error": result.error})
    else:
        logger.info(
            "tool.list_topics.ok",
            extra={"topic_count": len(result.data.get("topics", []))},
        )
    return result.data if result.ok else {"error": result.error}


async def set_language(language: str) -> dict[str, Any]:
    """Persist the user's preferred quiz language.

    Args:
        language: ISO 639-1 language code.

    The user identity is taken from the authenticated principal — MAF
    auto-derives the tool schema from this signature, so the model only
    sees ``language``. The `user_id` arg is injected from the principal
    before dispatch (wire concern, not model concern).
    """

    deps, principal = _ensure_initialised()
    result = await _TOOL_FNS["set_language"](
        {"user_id": principal.entra_oid, "language": language}, principal
    )
    return result.data if result.ok else {"error": result.error}


async def start_quiz(
    topic: str,
    language: str,
    n: int | None = None,
    difficulty: str | None = None,
    channel: str = "text",
) -> dict[str, Any]:
    """Create a quiz session, seed the shuffle, and return question 1.

    Args:
        topic: Topic ID (e.g. "azure-networking").
        language: ISO 639-1 language code.
        n: Number of questions (1..50). OPTIONAL — leave unset to use
            the topic's preconfigured ``default_n``. Only pass when the
            user volunteers an explicit count.
        difficulty: One of "easy", "medium", "hard", "mixed" (optional).
        channel: "text" or "voice".

    See :func:`set_language` for why ``user_id`` is not on the signature.
    """

    deps, principal = _ensure_initialised()
    args: dict[str, Any] = {
        "user_id": principal.entra_oid,
        "topic": topic,
        "language": language,
        "channel": channel,
    }
    if n is not None:
        args["n"] = n
    if difficulty:
        args["difficulty"] = difficulty
    result = await _TOOL_FNS["start_quiz"](args, principal)
    return result.data if result.ok else {"error": result.error}


async def submit_answer(
    session_id: str,
    question_id: str,
    raw_answer: str,
    channel: str = "text",
) -> dict[str, Any]:
    """Submit the user's answer for the current question.

    Args:
        session_id: The session ID returned by start_quiz.
        question_id: The question ID currently in flight.
        raw_answer: The user's raw answer text.
        channel: "text" or "voice".
    """

    deps, principal = _ensure_initialised()
    result = await _TOOL_FNS["submit_answer"](
        {
            "session_id": session_id,
            "question_id": question_id,
            "raw_answer": raw_answer,
            "channel": channel,
        },
        principal,
    )
    return result.data if result.ok else {"error": result.error}


async def get_results(session_id: str) -> dict[str, Any]:
    """Return the final score breakdown when the quiz is complete.

    Args:
        session_id: The session ID.

    See :func:`set_language` for why ``user_id`` is not on the signature.
    """

    deps, principal = _ensure_initialised()
    result = await _TOOL_FNS["get_results"](
        {"session_id": session_id, "user_id": principal.entra_oid}, principal
    )
    return result.data if result.ok else {"error": result.error}


# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------


async def _amain() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    project_endpoint = _require_env("FOUNDRY_PROJECT_ENDPOINT")
    agent_name = os.environ.get("AGENT_NAME", "fq-dev-agent")
    cosmos_endpoint = _require_env("COSMOS_ENDPOINT")
    search_endpoint = _require_env("SEARCH_ENDPOINT")

    # The user identity for tool-args: when running locally the deps
    # principal must match the authenticated identity. We let the user
    # override via env, otherwise we resolve it from the OID claim on
    # the runtime token.
    user_id = os.environ.get("CHAT_USER_ID") or os.environ.get("AZURE_CLIENT_ID") or "local-dev-user"

    # Lazy imports — agent_framework / azure.identity are heavy.
    from agent_framework.foundry import FoundryAgent  # noqa: PLC0415
    from azure.identity.aio import DefaultAzureCredential  # noqa: PLC0415

    credential = DefaultAzureCredential()

    # Build the shared tool deps. We use an async credential to construct
    # the search + Cosmos clients identically to how the production
    # container would.
    search_client = build_search_client(
        endpoint=search_endpoint,
        index_name=os.environ.get("SEARCH_INDEX_NAME", "questions"),
        credential=credential,
    )
    search = QuestionSearch(search_client)
    cosmos = CosmosRepository(endpoint=cosmos_endpoint, credential=credential)

    global _DEPS, _PRINCIPAL, _TOOL_FNS
    _DEPS = ToolDeps(repo=cosmos, search=search)
    _PRINCIPAL = Principal(entra_oid=user_id)
    _TOOL_FNS = build_tools(_DEPS)

    agent = FoundryAgent(
        project_endpoint=project_endpoint,
        agent_name=agent_name,
        credential=credential,
        tools=[list_topics, set_language, start_quiz, submit_answer, get_results],
    )

    print(f"Connected to agent {agent_name!r} at {project_endpoint}")
    print(f"User ID (set CHAT_USER_ID to override): {user_id}")
    print("Type your message and press Enter. Empty line or Ctrl-D exits.\n")

    try:
        while True:
            try:
                user_input = input("you> ").strip()
            except EOFError:
                print()  # newline after ^D
                break
            if not user_input:
                break

            try:
                response = await agent.run(user_input)
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent.run.failed")
                print(f"  [error] {exc}\n")
                continue

            text = getattr(response, "text", None) or str(response)
            print(f"agent> {text}\n")
    finally:
        await cosmos.close()
        await search_client.close()
        await credential.close()


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
