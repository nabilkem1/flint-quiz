# Seed Loader — one-shot Container Apps Job image.
#
# Runs `python -m src.seed.seed_index --env $AZURE_ENV_NAME --report` once,
# then exits. The Container Apps Job is invoked manually via
# `az containerapp job start` or wired into `make smoke`.
#
# Identity: the job runs as `uami-indexer-*` (Search Index Data Contributor
# + Storage Blob Data Reader). The loader script ABORTS if it detects
# `Search Service Contributor` on its identity — that's the control-plane
# role and would let a misconfigured loader drop indexes.

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

CMD ["python", "-m", "src.seed.seed_index", "--env", "dev", "--report"]
