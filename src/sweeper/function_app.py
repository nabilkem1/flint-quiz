"""Background sweeper Azure Function (TASK-191) — Functions host binding.

Wires :func:`src.sweeper._core.run_sweeper_tick` to a timer-trigger
(60-s tick). Use this host when you have ``Microsoft.Web/serverFarms``
quota and prefer Functions' billing / observability story. On
subscriptions where the quota is 0, deploy the Container Apps Job
variant (``src.sweeper.__main__``) instead — same sweep logic, no VM.

The pure sweep code lives in :mod:`src.sweeper._core` so both hosts
share one source of truth for the state-machine transitions, scope
guard, and 412-race handling.
"""

from __future__ import annotations

import os

import azure.functions as func  # type: ignore[import-not-found]
from azure.identity.aio import DefaultAzureCredential

from src.data.cosmos_repository import CosmosRepository
from src.observability.telemetry import TelemetryConfig, initialise_telemetry
from src.sweeper._core import (
    DEFAULT_MAX_STRANDED_SECONDS,
    DEFAULT_PAUSE_THRESHOLD_SECONDS,
    FEED_QUERY_AGE_SECONDS,
    SweeperConfig,
    logger,
    run_sweeper_tick,
)

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 */1 * * * *",  # every 60s — TASK-191 (Functions ncron, 6-field)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False,
)
async def sweeper_tick(timer: func.TimerRequest) -> None:
    """Function entry point — runs every 60 seconds."""

    if timer.past_due:
        logger.warning("sweeper.past_due_tick")

    # Idempotent; only the first call wires OTel. See `_core._record_metrics`
    # for why this must run before `run_sweeper_tick`.
    initialise_telemetry(
        TelemetryConfig(
            connection_string=os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"),
            service_name="flint-quiz-sweeper",
        ),
        enable_foundry_tracing=False,
    )

    cfg = SweeperConfig()
    credential = DefaultAzureCredential()
    repo = CosmosRepository(
        endpoint=cfg.cosmos_endpoint,
        database_name=cfg.database,
        sessions_container=cfg.sessions_container,
        credential=credential,
    )
    try:
        await run_sweeper_tick(cfg, repo)
    finally:
        # `repo.close()` closes the CosmosClient and the credential it owns.
        await repo.close()


__all__ = [
    "DEFAULT_MAX_STRANDED_SECONDS",
    "DEFAULT_PAUSE_THRESHOLD_SECONDS",
    "FEED_QUERY_AGE_SECONDS",
    "SweeperConfig",
    "app",
    "run_sweeper_tick",
]
