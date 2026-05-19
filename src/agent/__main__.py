"""Quiz Agent runtime entry point — registers the Foundry agent and runs the
dispatcher daemon.

Three responsibilities (in order):

  1. **Register** the agent on the Foundry project. Idempotent — re-runs
     find the existing agent by canonical name (`fq-<env>-agent`) and
     reuse it. The five tools' descriptors are taken from
     ``src.agent.dispatcher.ALLOWED_TOOLS``.

  2. **Listen** for tool-call events from Foundry runs (``runs`` API,
     ``requires_action`` events). When the model emits a
     ``submit_tool_outputs`` requirement, the dispatcher executes the
     tool body locally and posts the result back.

  3. **Liveness** — a tiny TCP listener on ``$PORT`` so Container Apps'
     probe can verify the daemon is up.

The daemon exits with a clear error if either:

  * The required env (`FOUNDRY_PROJECT_ENDPOINT`, `AZURE_CLIENT_ID`,
    `APP_INSIGHTS_CONNECTION_STRING`, `COSMOS_ENDPOINT`,
    `SEARCH_ENDPOINT`) is missing.
  * The Foundry project rejects the agent registration (e.g., the model
    deployment is missing).

The actual MAF runtime polling loop is a lightweight ``asyncio`` worker
the dispatcher posts results back through. If the Foundry SDK isn't
installed in this environment (test, local-dev), the daemon registers
nothing and just keeps the liveness probe up — useful for the
``azd deploy`` no-op smoke.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

from src.agent.dispatcher import ALLOWED_TOOLS
from src.observability.telemetry import TelemetryConfig, initialise_telemetry

logger = logging.getLogger("flint-quiz.agent")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required env var: {name}")
    return value


def _optional_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# Foundry agent registration
# ---------------------------------------------------------------------------


async def register_foundry_agent() -> str | None:
    """Idempotent get-or-create of the Foundry agent record.

    Uses the Azure AI Projects v2 SDK:

      * ``agents.get(agent_name)`` to check for an existing record. A
        404 (or any exception) is treated as "not found" — we fall
        through to create.
      * ``agents.create_version(agent_name, definition=PromptAgentDefinition(...))``
        creates the agent + first version in one call. `definition`
        carries the model deployment + instructions + tools.

    Returns the agent's `name` on success, `None` when the SDK is
    unavailable (test / local-dev) or registration fails. Failures are
    **non-fatal**: the dispatcher daemon stays alive so an operator
    can re-iterate without redeploying the container.
    """

    try:
        from azure.ai.projects.aio import AIProjectClient  # noqa: PLC0415
        from azure.ai.projects.models import (  # noqa: PLC0415
            FunctionTool,
            MCPTool,
            PromptAgentDefinition,
        )
        from azure.identity.aio import DefaultAzureCredential  # noqa: PLC0415
    except ImportError:
        logger.warning(
            "agent.register.sdk_missing",
            extra={"hint": "azure-ai-projects not installed; skipping registration"},
        )
        return None

    project_endpoint = _require_env("FOUNDRY_PROJECT_ENDPOINT")
    agent_name = _optional_env("AGENT_NAME", "fq-dev-agent")
    # Prefer the chat-capable deployment (gpt-4o-mini) for the PromptAgentDefinition.
    # `MODEL_DEPLOYMENT_NAME` is the realtime/voice deployment (gpt-realtime)
    # which rejects /chat/completions and /responses — using it here would
    # render the Foundry Playground unable to roundtrip text. Fall back to
    # MODEL_DEPLOYMENT_NAME only when the chat var isn't set (older envs).
    model_deployment = (
        os.environ.get("CHAT_MODEL_DEPLOYMENT_NAME")
        or _require_env("MODEL_DEPLOYMENT_NAME")
    )

    instructions = _default_instructions()

    # Build the v2 SDK `FunctionTool` list once.
    #
    # `parameters` must be a real JSON Schema. An empty schema (`{}`) makes
    # the model invoke every tool with `{}`, which fails Pydantic
    # validation on the way back in — the chat loop then burns its
    # consecutive-error budget without ever reaching tool execution.
    # We derive each tool's schema from its Pydantic request model so the
    # model knows exactly which fields are required.
    from src.data.models import (  # noqa: PLC0415 — local import keeps SDK-missing branch above clean
        GetResultsRequest,
        ListTopicsRequest,
        SetLanguageRequest,
        StartQuizRequest,
        SubmitAnswerRequest,
    )

    descriptions = {
        "list_topics": "Return the catalog of available quiz topics with localized labels.",
        "set_language": "Persist the user's preferred quiz language. ISO 639-1 only.",
        "start_quiz": "Create a session, seed the shuffle, and return question 1.",
        "submit_answer": (
            "Submit the user's answer for the current question; "
            "grading is server-side."
        ),
        "get_results": "Return the final score breakdown when the quiz is complete.",
    }
    request_models = {
        "list_topics": ListTopicsRequest,
        "set_language": SetLanguageRequest,
        "start_quiz": StartQuizRequest,
        "submit_answer": SubmitAnswerRequest,
        "get_results": GetResultsRequest,
    }
    tools: list = [
        FunctionTool(
            type="function",
            name=name,
            description=descriptions[name],
            parameters=request_models[name].model_json_schema(),
            strict=False,
        )
        for name in sorted(ALLOWED_TOOLS)
    ]

    # If a public MCP server URL is configured (the third Container App
    # — `mcp-server` — exposes /mcp over HTTPS), ALSO register an MCPTool
    # entry so the Foundry Playground can reach the same 5 tools without
    # needing an external client. The chat CLI / MAF path keeps using
    # the function-tool entries above; the MCP path adds Playground
    # capability without removing the function path. Both paths share
    # `build_tools(deps)` as the actual execution body.
    mcp_url = os.environ.get("MCP_SERVER_URL", "").strip()
    mcp_connection_name = os.environ.get("MCP_CONNECTION_NAME", "").strip()
    if mcp_url and mcp_connection_name:
        # `project_connection_id` is what tells Foundry to authenticate to
        # our /mcp endpoint with the connection's stored auth (AAD →
        # project-managed-identity). Without it, Foundry calls anonymously
        # and our server-side `src/mcp/auth.py` returns 401.
        tools.append(
            MCPTool(
                type="mcp",
                server_label="flint-quiz-tools",
                server_url=mcp_url,
                require_approval="never",
                server_description=(
                    "Flint Quiz tool surface — list_topics, set_language, "
                    "start_quiz, submit_answer, get_results."
                ),
                project_connection_id=mcp_connection_name,
            )
        )
        logger.info(
            "agent.register.mcp_tool_added",
            extra={
                "server_url": mcp_url,
                "server_label": "flint-quiz-tools",
                "connection_name": mcp_connection_name,
            },
        )

    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=project_endpoint, credential=credential)

    async with client:
        agents = client.agents

        # 1. Idempotency — try a get-by-name. 404 → fall through to create.
        existing_id: str | None = None
        try:
            existing = await agents.get(agent_name)
            existing_id = getattr(existing, "name", None) or getattr(existing, "id", None)
        except Exception:  # noqa: BLE001 — 404 / not-found is the expected branch on first deploy
            logger.info(
                "agent.register.not_found",
                extra={"agent_name": agent_name, "hint": "will attempt create"},
            )

        if existing_id:
            logger.info(
                "agent.register.reused",
                extra={"agent_id": existing_id, "agent_name": agent_name},
            )
            # Even on reuse, push a new version so the freshly-built
            # tool set + instructions take effect without an out-of-band
            # portal edit. `create_version` on an existing agent appends
            # rather than replacing, and the new version becomes default.
            try:
                definition = PromptAgentDefinition(
                    kind="prompt",
                    model=model_deployment,
                    instructions=instructions,
                    tools=tools,
                )
                version = await agents.create_version(
                    agent_name=agent_name,
                    definition=definition,
                )
                logger.info(
                    "agent.register.version_appended",
                    extra={
                        "agent_name": agent_name,
                        "version": getattr(version, "version", "?"),
                    },
                )
            except Exception:  # noqa: BLE001 — non-fatal; reuse is good enough
                logger.warning(
                    "agent.register.version_append_failed",
                    exc_info=True,
                    extra={"agent_name": agent_name},
                )
            return existing_id

        # 2. Create — `create_version` creates the agent record on first
        # call (if it doesn't exist) AND a v1 version pointing at our
        # PromptAgentDefinition.
        try:
            definition = PromptAgentDefinition(
                kind="prompt",
                model=model_deployment,
                instructions=instructions,
                tools=tools,
            )
            response = await agents.create_version(
                agent_name=agent_name,
                definition=definition,
            )
            new_id = (
                getattr(response, "name", None)
                or getattr(response, "id", None)
                or agent_name
            )
            logger.info(
                "agent.register.created",
                extra={
                    "agent_id": new_id,
                    "agent_name": agent_name,
                    "version": getattr(response, "version", "?"),
                    "tool_count": len(tools),
                },
            )
            return new_id
        except Exception:  # noqa: BLE001 — non-fatal
            logger.exception(
                "agent.register.create_failed_nonfatal",
                extra={
                    "agent_name": agent_name,
                    "hint": (
                        "Container stays alive on the liveness probe; "
                        "rerun the entry point after iterating on the SDK call."
                    ),
                },
            )
            return None


def _default_instructions() -> str:
    """Static identity blurb; mirrors `quiz_agent._default_static_instructions`."""

    return (
        "You are Flint, a conversational quiz host. You operate inside a single "
        "Foundry Hosted Agent serving both text and voice. Per-session governance "
        "(language, refusal copy, behavioural contract) is applied via the per-session "
        "system message; this static blurb is the identity-only header. You never grade; "
        "the `submit_answer` tool grades and persists. The dispatcher rejects any tool "
        "name outside the five-tool allowlist; do not attempt others."
    )


# ---------------------------------------------------------------------------
# Liveness probe (Container Apps health-check)
# ---------------------------------------------------------------------------


async def _liveness_server(port: int) -> None:
    """Accept-and-close TCP listener — enough for Container Apps' probe.

    Container Apps' default probe uses TCP. A more sophisticated readiness
    probe (e.g., checks AppConfig + Cosmos reachability) is a follow-up.
    """

    async def _handle(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    server = await asyncio.start_server(_handle, "0.0.0.0", port)
    logger.info("agent.liveness.listening", extra={"port": port})
    async with server:
        await server.serve_forever()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def _amain() -> None:
    initialise_telemetry(
        TelemetryConfig(
            connection_string=os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"),
            service_instance_id=_optional_env("CONTAINER_APP_REVISION", "local"),
        )
    )

    agent_id = await register_foundry_agent()
    logger.info("agent.started", extra={"agent_id": agent_id})

    port = int(_optional_env("PORT", "8080"))
    liveness = asyncio.create_task(_liveness_server(port))

    # Graceful shutdown on SIGTERM (Container Apps sends this during
    # revision swaps).
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover — Windows
            pass

    await stop.wait()
    logger.info("agent.shutdown.signal_received")
    liveness.cancel()
    try:
        await liveness
    except asyncio.CancelledError:
        pass


def main() -> None:
    try:
        asyncio.run(_amain())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.info("agent.shutdown.keyboard_interrupt")
    except Exception:  # noqa: BLE001
        logger.exception("agent.shutdown.unhandled_exception")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
