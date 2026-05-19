"""JWT validation for the MCP server's `/mcp` endpoint.

Foundry's runtime calls our MCP server using a Managed Identity token —
either the Foundry project's system-assigned MI or the agent's UAMI,
depending on how the MCP connection is configured on the project.

We validate the incoming `Authorization: Bearer <JWT>` against:

  1. Signature (RS256 via Entra's JWKS for our tenant).
  2. Issuer (`https://login.microsoftonline.com/<tenant>/v2.0` or the
     v1 sts.windows.net form — both are accepted).
  3. Caller principal — the ``oid`` claim must match one of the
     allowlisted MIs configured at deploy time
     (``MCP_TRUSTED_PRINCIPAL_OIDS``, comma-separated).

Audience checking is intentionally lax (`verify_aud=False`) because the
MI → MI flow uses Entra-issued tokens whose audience varies by Foundry's
internal contract — we trust the principal instead. The signature +
issuer + allowlist together still provide a strong identity proof.

The validated ``oid`` becomes the ``Principal.entra_oid`` we pass to the
tool dispatchers, so each tool body sees the same identity surface it
does when called from the chat CLI.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

_ENV_TENANT_ID = "AZURE_TENANT_ID"
_ENV_TRUSTED_OIDS = "MCP_TRUSTED_PRINCIPAL_OIDS"


@lru_cache(maxsize=1)
def _trusted_oids() -> frozenset[str]:
    """Comma-separated allowlist of Entra OIDs the MCP server accepts.

    Wired at deploy time from `infra/modules/mcp-server-app.bicep`:
    the Foundry account's system-assigned MI principal ID, plus the
    agent UAMI principal ID (handy when running the chat CLI as the
    UAMI in CI scenarios). Empty allowlist = REJECT EVERYONE.
    """

    raw = os.environ.get(_ENV_TRUSTED_OIDS, "")
    return frozenset(oid.strip() for oid in raw.split(",") if oid.strip())


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    tenant_id = os.environ[_ENV_TENANT_ID]
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
    )


def _expected_issuers() -> tuple[str, ...]:
    tenant_id = os.environ[_ENV_TENANT_ID]
    return (
        f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        f"https://sts.windows.net/{tenant_id}/",
    )


async def require_foundry_caller(
    authorization: str = Header(default=""),
) -> str:
    """Validate the bearer token and return the caller's ``oid`` claim.

    Raises:
        HTTPException(401): missing / malformed / expired / untrusted token.

    Returns:
        The validated ``oid`` claim — used as the Principal for tool dispatch.
    """

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header",
        )
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="empty bearer token",
        )

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
            issuer=list(_expected_issuers()),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired") from None
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="unexpected issuer") from None
    except jwt.PyJWTError as exc:
        logger.warning("mcp.auth.jwt_invalid", extra={"error": str(exc)})
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from None

    oid = claims.get("oid") or claims.get("sub")
    if not oid:
        raise HTTPException(status_code=401, detail="token missing oid/sub claim")

    allowed = _trusted_oids()
    if not allowed:
        # Defence in depth — refuse to start up effectively open.
        logger.error("mcp.auth.no_allowlist", extra={"env": _ENV_TRUSTED_OIDS})
        raise HTTPException(
            status_code=503,
            detail=f"MCP server misconfigured: {_ENV_TRUSTED_OIDS} is empty",
        )
    if oid not in allowed:
        # NOTE: print + logger.warning — uvicorn's default logging filter
        # drops application-level WARNINGs unless we configure them
        # explicitly. The print() guarantees stdout capture by Container
        # Apps regardless of how the logger is wired.
        print(
            f"MCP_AUTH_REJECT incoming_oid={oid!r} "
            f"allowlist_count={len(allowed)} "
            f"allowlist_sample={list(allowed)[:2]!r}",
            flush=True,
        )
        logger.warning(
            "mcp.auth.untrusted_principal",
            extra={"oid_prefix": oid[:8], "allowed_count": len(allowed)},
        )
        raise HTTPException(status_code=403, detail="caller not in MCP allowlist")

    return oid
