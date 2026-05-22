"""API-key authentication for the MCP server's `/mcp` endpoint.

Foundry's Playground refuses to forward any Entra-issued token to a custom
MCP endpoint (`tool_user_error: Cannot pass Microsoft token to untrusted
MCP endpoint or connector`). We use a shared API key on `X-API-Key`
instead — Foundry's MCP connection is configured with `CustomKeys` auth
(see `infra/modules/foundry-mcp-connection.bicep`) and attaches the same
key to every outgoing request.

Server-side: we read the expected value from `MCP_API_KEY` (a Container
Apps secret) and compare with `hmac.compare_digest` for constant-time
match.

Caller identity: with a static key, there's no per-caller Entra OID to
hand the dispatcher. The MCP transport injects `user_id` into tool
arguments from the JSON-RPC `arguments` payload, so the dispatcher's
`Principal` here is a fixed sentinel — the actual per-user identity flows
through the tool args (set on the agent side from the conversation
context).
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

_ENV_API_KEY = "MCP_API_KEY"

# Sentinel principal returned when the API-key check passes. The real
# per-user identity flows through tool args (see `src/mcp/server.py`
# `tools/call` handler, which threads `user_id` from the JSON-RPC
# arguments into the Principal before dispatch).
MCP_CALLER_OID = "mcp-shared-key-caller"


async def require_foundry_caller(
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> str:
    """Validate the `X-API-Key` header and return a sentinel caller id.

    Raises:
        HTTPException(401): missing or mismatched key.
        HTTPException(503): server misconfigured (no key set in env).

    Returns:
        A fixed sentinel string — the per-user identity is carried in
        the tool's `user_id` argument, not in the transport header.
    """

    expected = os.environ.get(_ENV_API_KEY, "")
    if not expected:
        logger.error("mcp.auth.no_key_configured", extra={"env": _ENV_API_KEY})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"MCP server misconfigured: {_ENV_API_KEY} is empty",
        )

    presented = (x_api_key or "").strip()
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-API-Key header",
        )

    if not hmac.compare_digest(presented, expected):
        logger.warning("mcp.auth.key_mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid X-API-Key",
        )

    return MCP_CALLER_OID
