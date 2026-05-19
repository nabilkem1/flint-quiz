"""MCP server — JSON-RPC over HTTP, exposing the 5 quiz tools to Foundry.

The Foundry runtime POSTs JSON-RPC envelopes to ``/mcp``:

  * ``initialize``  — handshake; we respond with capabilities + serverInfo.
  * ``tools/list``  — list available tools and their JSON Schemas.
  * ``tools/call``  — execute a tool with the given args; return its
    JSON-serialised result wrapped in ``TextContent``.

The tool bodies are the same ``build_tools(deps)`` callables that
``src/agent/chat.py`` wires through MAF — single source of truth.

Liveness / readiness: ``GET /healthz`` returns 200 once `lifespan`
has connected Cosmos + Search.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from fastapi import Depends, FastAPI, Request

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.data.cosmos_repository import CosmosRepository
from src.data.models import (
    GetResultsRequest,
    ListTopicsRequest,
    SetLanguageRequest,
    StartQuizRequest,
    SubmitAnswerRequest,
)
from src.data.question_search import QuestionSearch, build_search_client
from src.data.tool_schemas import public_input_schema
from src.mcp.auth import require_foundry_caller
from src.observability.telemetry import TelemetryConfig, initialise_telemetry

logger = logging.getLogger("mcp.server")


# Pydantic request models — `model_json_schema()` is what we expose to
# Foundry so the model knows what each tool takes. Same models the agent
# registration uses; keep them as the single source of truth.
REQUEST_MODELS = {
    "list_topics": ListTopicsRequest,
    "set_language": SetLanguageRequest,
    "start_quiz": StartQuizRequest,
    "submit_answer": SubmitAnswerRequest,
    "get_results": GetResultsRequest,
}

DESCRIPTIONS = {
    "list_topics": "Return the catalog of available quiz topics with localized labels.",
    "set_language": "Persist the user's preferred quiz language. ISO 639-1 only.",
    "start_quiz": "Create a session, seed the shuffle, and return question 1.",
    "submit_answer": (
        "Submit the user's answer for the current question; grading is server-side."
    ),
    "get_results": "Return the final score breakdown when the quiz is complete.",
}


# Module-level state — populated once in the lifespan ctx.
_state: dict[str, Any] = {"deps": None, "tools": None, "credential": None}


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    initialise_telemetry(
        TelemetryConfig(
            connection_string=os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"),
            service_name="flint-quiz-mcp",
        ),
        enable_foundry_tracing=False,
    )

    credential = DefaultAzureCredential()
    search_client = build_search_client(
        endpoint=os.environ["SEARCH_ENDPOINT"],
        index_name=os.environ.get("SEARCH_INDEX_NAME", "questions"),
        credential=credential,
    )
    repo = CosmosRepository(
        endpoint=os.environ["COSMOS_ENDPOINT"], credential=credential
    )
    deps = ToolDeps(repo=repo, search=QuestionSearch(search_client))
    _state["deps"] = deps
    _state["tools"] = build_tools(deps)
    _state["credential"] = credential
    _state["search_client"] = search_client
    _state["repo"] = repo
    logger.info("mcp.server.started", extra={"tool_count": len(_state["tools"])})
    try:
        yield
    finally:
        await repo.close()
        await search_client.close()
        await credential.close()


app = FastAPI(lifespan=_lifespan, title="flint-quiz-mcp")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "ready": _state.get("tools") is not None}


@app.post("/mcp")
async def mcp_endpoint(
    request: Request,
    caller_oid: str = Depends(require_foundry_caller),
) -> dict[str, Any]:
    """JSON-RPC 2.0 endpoint — handles every MCP method."""

    body = await request.json()
    method = body.get("method")
    rpc_id = body.get("id")
    params = body.get("params") or {}

    logger.info(
        "mcp.rpc.received",
        extra={"method": method, "caller_oid_prefix": caller_oid[:8]},
    )

    if method == "initialize":
        return _ok(rpc_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "flint-quiz-mcp", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        # Per MCP spec, this is a notification (no `id`, no response).
        return _ok(rpc_id, {}) if rpc_id is not None else {"jsonrpc": "2.0"}

    if method == "tools/list":
        # `public_input_schema` strips `user_id` — see `src.data.tool_schemas`
        # for the rationale. The override below re-injects it from the
        # authenticated caller before dispatch.
        tools_list = [
            {
                "name": name,
                "description": DESCRIPTIONS[name],
                "inputSchema": public_input_schema(model),
            }
            for name, model in REQUEST_MODELS.items()
        ]
        return _ok(rpc_id, {"tools": tools_list})

    if method == "tools/call":
        tool_name = params.get("name")
        args = dict(params.get("arguments") or {})
        if tool_name not in _state["tools"]:
            return _error(rpc_id, -32601, f"unknown tool: {tool_name!r}")

        # `user_id` is stripped from the published schema (see
        # `tools/list` above) so the model never sees / sends it. We
        # inject it here from the authenticated principal for every
        # tool whose request model declares it — keeps the dispatcher's
        # `request.user_id != principal.entra_oid` check trivially true
        # and centralises the user-identity flow at the wire boundary.
        request_model = REQUEST_MODELS.get(tool_name)
        if request_model is not None and "user_id" in request_model.model_fields:
            args["user_id"] = caller_oid

        principal = Principal(entra_oid=caller_oid)
        try:
            result = await _state["tools"][tool_name](args, principal)
        except Exception as exc:  # noqa: BLE001 — boundary; surface to client
            logger.exception("mcp.tool.unhandled", extra={"tool": tool_name})
            return _ok(rpc_id, {
                "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                "isError": True,
            })

        if not result.ok:
            return _ok(rpc_id, {
                "content": [
                    {"type": "text", "text": json.dumps({"error": result.error})}
                ],
                "isError": True,
            })
        return _ok(rpc_id, {
            "content": [
                {"type": "text", "text": json.dumps(result.data, default=str)}
            ],
        })

    return _error(rpc_id, -32601, f"method not found: {method!r}")


def _ok(rpc_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
