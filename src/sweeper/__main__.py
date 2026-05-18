"""Container Apps Job entry point — one sweep tick, then exit.

Invoked by the CAJ scheduler every minute (cron ``*/1 * * * *``). Each
firing is a fresh container; the Python process never loops. Exit codes:

* ``0`` — clean tick (zero or more rows transitioned). 412 races and
  malformed rows are logged-and-skipped inside ``run_sweeper_tick`` and
  do NOT fail the job.
* ``1`` — hard failure (config invalid, Cosmos unreachable, unhandled
  exception). CAJ will retry per the ``replicaRetryLimit`` policy.

The sweep logic itself lives in :mod:`src.sweeper._core`. This module
adds nothing beyond identity resolution, the Cosmos repo, and the
exit-code contract — keep it that way.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from azure.identity.aio import DefaultAzureCredential

from src.data.cosmos_repository import CosmosRepository
from src.observability.telemetry import TelemetryConfig, initialise_telemetry
from src.sweeper._core import SweeperConfig, run_sweeper_tick

logger = logging.getLogger("sweeper.job")


async def _amain() -> int:
    # Initialise OTel BEFORE `run_sweeper_tick` so the four `sweeper.*`
    # counters bind to the real Azure-Monitor MeterProvider. Skipping this
    # leaves them tied to the OTel NoOp default and metrics never leave
    # the container.
    initialise_telemetry(
        TelemetryConfig(
            connection_string=os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"),
            service_name="flint-quiz-sweeper",
            service_instance_id=os.environ.get("CONTAINER_APP_REPLICA_NAME", "local"),
        ),
        # Foundry tracing is irrelevant here — the sweeper never calls
        # the Foundry runtime, so opt out to keep boot snappy.
        enable_foundry_tracing=False,
    )

    cfg = SweeperConfig()  # raises if SWEEPER_ALLOWED_CONTAINER != "sessions"
    credential = DefaultAzureCredential()
    repo = CosmosRepository(
        endpoint=cfg.cosmos_endpoint,
        database_name=cfg.database,
        sessions_container=cfg.sessions_container,
        credential=credential,
    )
    try:
        counters = await run_sweeper_tick(cfg, repo)
        logger.info("sweeper.job.done", extra=counters)
        return 0
    finally:
        await repo.close()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001 — boundary: surface as non-zero exit
        logger.exception("sweeper.job.unhandled")
        return 1


if __name__ == "__main__":
    sys.exit(main())
