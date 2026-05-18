# Sweeper — one-shot Container Apps Job image.
#
# CAJ scheduler fires every minute (cron `*/1 * * * *`). Each firing
# runs `python -m src.sweeper`, which executes `run_sweeper_tick` once
# and exits. No internal timer loop, no Functions runtime.
#
# Identity: the job runs as `uami-agent-*` (Cosmos Data Contributor
# scoped to the `sessions` container — same RBAC the Functions host
# would have used). `_core.SweeperConfig` re-asserts the scope guard
# at boot (`SWEEPER_ALLOWED_CONTAINER == "sessions"`).

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

CMD ["python", "-m", "src.sweeper"]
