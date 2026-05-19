"""OpenTelemetry initialisation (TASK-140 / NFR-008).

A thin, idempotent wrapper around ``azure-monitor-opentelemetry`` that
the agent boot path calls **exactly once** at startup. Three concerns:

  1. **Connection string**: pulled from the env var
     ``APPLICATIONINSIGHTS_CONNECTION_STRING``. The connection string
     is NOT a secret (per Microsoft guidance; mirrored in
     ``docs/secrets.md``). Anywhere else routes through Key Vault.
  2. **Foundry tracing**: enabled via the SDK's
     ``configure_azure_monitor`` call — diagnostic settings on the
     Foundry account were provisioned in 001-infrastructure TASK-009.
     Per-thread / per-tool spans appear in App Insights once this
     function has run.
  3. **No-op fallback**: in tests + local-dev (no env, no SDK), the
     initialiser logs a single info line and returns. It NEVER raises;
     the agent must remain runnable without App Insights wiring.

The init function is **idempotent**. Re-calling it is safe — the SDK
guards against double-instrumentation and we keep a module-level flag
so the boot order can call ``initialise_telemetry`` from multiple
entry points (CLI, MAF runtime, sweeper) without trampling.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from threading import Lock
from typing import Mapping

logger = logging.getLogger(__name__)

# Microsoft-documented env var name; do NOT rename — keeping the literal
# name lets ops set it from `azd env get-values` without translation.
_CONN_STRING_ENV: str = "APPLICATIONINSIGHTS_CONNECTION_STRING"


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    """Configuration surface for the telemetry initialiser.

    Tests construct this with `connection_string=None` (no real export);
    production reads from the env at boot time.
    """

    connection_string: str | None
    service_name: str = "flint-quiz"
    service_namespace: str = "flint"
    service_instance_id: str | None = None
    extra_resource_attributes: Mapping[str, str] = field(default_factory=dict)


_LOCK: Lock = Lock()
_INITIALISED: bool = False


def initialise_telemetry(
    config: TelemetryConfig | None = None,
    *,
    enable_foundry_tracing: bool = True,
) -> bool:
    """Initialise OpenTelemetry. Returns True if export is wired.

    Safe to call multiple times — subsequent calls are no-ops. When the
    connection string is missing (tests, local dev), the function logs
    a single info line and returns False so callers can branch on the
    result if they want to.
    """

    global _INITIALISED
    with _LOCK:
        if _INITIALISED:
            logger.debug("telemetry.initialise: already initialised; no-op")
            return True

        cfg = config or _config_from_env()
        if not cfg.connection_string:
            logger.info(
                "telemetry.initialise.no_connection_string",
                extra={"env_var": _CONN_STRING_ENV},
            )
            _INITIALISED = True
            return False

        try:
            _configure_azure_monitor(cfg, enable_foundry_tracing=enable_foundry_tracing)
        except Exception:  # noqa: BLE001
            # An initialisation failure on a hot path is preferable to
            # raising — the agent must remain runnable even if telemetry
            # is unhealthy. We log loudly so the operator notices.
            logger.exception(
                "telemetry.initialise.failed",
                extra={"service_name": cfg.service_name},
            )
            _INITIALISED = True
            return False

        _INITIALISED = True
        logger.info(
            "telemetry.initialised",
            extra={
                "service_name": cfg.service_name,
                "service_namespace": cfg.service_namespace,
                "foundry_tracing": enable_foundry_tracing,
            },
        )
        return True


def reset_for_tests() -> None:
    """Test-only — drop the initialised flag so a subsequent test can
    re-run the initialiser. Production code MUST NOT call this."""

    global _INITIALISED
    with _LOCK:
        _INITIALISED = False


def _config_from_env() -> TelemetryConfig:
    return TelemetryConfig(
        connection_string=os.environ.get(_CONN_STRING_ENV),
        service_name=os.environ.get("OTEL_SERVICE_NAME", "flint-quiz"),
        service_namespace=os.environ.get("OTEL_SERVICE_NAMESPACE", "flint"),
        service_instance_id=os.environ.get("OTEL_SERVICE_INSTANCE_ID"),
    )


def _configure_azure_monitor(
    cfg: TelemetryConfig, *, enable_foundry_tracing: bool
) -> None:
    """Call `azure-monitor-opentelemetry.configure_azure_monitor` lazily.

    Lazy import keeps test environments importable without the Azure
    SDK installed. Production wiring has the package on the lock file
    (`pyproject.toml`).
    """

    # Lazy import — production has the package; tests skip past this
    # branch entirely because `cfg.connection_string is None`.
    from azure.monitor.opentelemetry import configure_azure_monitor  # noqa: PLC0415

    resource_attributes: dict[str, str] = {
        "service.name": cfg.service_name,
        "service.namespace": cfg.service_namespace,
    }
    if cfg.service_instance_id:
        resource_attributes["service.instance.id"] = cfg.service_instance_id
    for key, value in cfg.extra_resource_attributes.items():
        resource_attributes.setdefault(key, value)

    # The App Insights workspace deploys with `DisableLocalAuth=true`, so
    # the OTel exporter MUST use an Entra credential — Instrumentation-
    # Key-only auth returns 401 on every batch. The credential resolves
    # to the runtime UAMI (the container sets `AZURE_CLIENT_ID`).
    credential: object | None = None
    try:
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415 - lazy

        credential = DefaultAzureCredential()
    except ImportError:  # pragma: no cover — fallback for tests
        credential = None

    configure_kwargs: dict[str, object] = {
        "connection_string": cfg.connection_string,
        "resource_attributes": resource_attributes,
        # Enabling tracing surfaces the Foundry thread/tool spans
        # alongside our own (NFR-008). The SDK's defaults already
        # capture HTTP + Cosmos client spans; we explicitly opt in for
        # the agent-level spans below.
        "enable_live_metrics": True,
    }
    if credential is not None:
        configure_kwargs["credential"] = credential

    configure_azure_monitor(**configure_kwargs)
    if enable_foundry_tracing:
        _enable_foundry_tracing()


def _enable_foundry_tracing() -> None:
    """Hook the Foundry SDK's tracing toggle, if available.

    Foundry / MAF emit thread + tool spans through the OpenTelemetry
    SDK when their tracing flag is set. The flag is set via env at
    deploy time (`AZURE_AI_TRACING_ENABLED=true`); we re-assert it here
    so a missing env doesn't silently disable tracing in production.
    """

    if os.environ.get("AZURE_AI_TRACING_ENABLED", "").lower() == "false":
        # An operator explicitly turned this off — respect it.
        return
    os.environ.setdefault("AZURE_AI_TRACING_ENABLED", "true")


__all__ = [
    "TelemetryConfig",
    "initialise_telemetry",
    "reset_for_tests",
]
