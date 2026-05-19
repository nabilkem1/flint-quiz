# Quiz Agent — long-running dispatcher daemon container.
#
# The image registers the Foundry agent (idempotent get-or-create) and then
# runs the MAF tool-call dispatcher loop. Tools execute inside this process
# under the runtime UAMI; durable state lives in Cosmos.
#
# Build context is the repo root (.). The Dockerfile is referenced from
# `azure.yaml:services.quiz-agent.docker.path`.

# ---- 1. builder ----------------------------------------------------------
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install the project + runtime deps into a clean prefix so the runtime
# image carries only what's needed.
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --upgrade pip \
 && python -m pip install --prefix=/install .

# ---- 2. runtime ----------------------------------------------------------
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

# Non-root user. Container Apps already runs as non-root, but explicit
# beats implicit: drops capabilities at process boundary.
RUN groupadd --system --gid 10001 flint \
 && useradd --system --uid 10001 --gid flint --create-home --shell /usr/sbin/nologin flint

WORKDIR /app

# Bring the wheels installed by the builder stage. `--prefix` installs into
# `/install/lib/...` which we drop into the runtime's `/usr/local`.
COPY --from=builder /install /usr/local
COPY --chown=flint:flint src ./src

USER flint
EXPOSE 8080

# Health-check hits a tiny TCP listener the entry point opens for Container
# Apps' liveness probe. The agent's actual work is the dispatcher loop;
# the listener is plumbing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', 8080)); s.close()" || exit 1

# Entrypoint: register + run dispatcher daemon. See src/agent/__main__.py.
CMD ["python", "-m", "src.agent"]
