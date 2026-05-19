# MCP Server — FastAPI HTTP endpoint exposing the 5 quiz tools to Foundry.
#
# Same multi-stage Python 3.12 base as the other services so the wheel
# set is consistent. CMD launches uvicorn against `src.mcp.server:app`.
#
# Identity: the container runs as `uami-agent-*` (Cosmos Data Contributor
# + AI Search reader). On incoming requests, `src/mcp/auth.py` validates
# the caller's Entra token and matches the `oid` claim against the
# MCP_TRUSTED_PRINCIPAL_OIDS env var.

# ---- 1. builder ----------------------------------------------------------
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --upgrade pip \
 && python -m pip install --prefix=/install .

# ---- 2. runtime ----------------------------------------------------------
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 flint \
 && useradd --system --uid 10001 --gid flint --create-home --shell /usr/sbin/nologin flint

WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=flint:flint src ./src

USER flint
EXPOSE 8080

CMD ["python", "-m", "uvicorn", "src.mcp.server:app", "--host", "0.0.0.0", "--port", "8080"]
