"""Cost dashboard helpers (TASK-147 / NFR-013 / §007-operational-runbook §5).

The cost surface is **read-only** — Azure Cost Management is the source
of truth; this module provides the small set of named KQL queries the
"Quiz Cost" workbook embeds. Keeping the queries in code (rather than
inline in the Bicep) lets tests grep them for stable shapes and
prevents the workbook's deserialised JSON from drifting under our nose.

Each constant is a single-line KQL clause the workbook can paste
into a `query:` field — no parameters baked in. The workbook supplies
its own time range.
"""

from __future__ import annotations

# Number of Realtime audio minutes per session (NFR-013 anchor). Driven
# from the `voice.session_bound` and `voice.session_closed` events
# emitted by `src/voice/realtime_runtime.py` — both carry `session_id`
# and the elapsed seconds.
REALTIME_MINUTES_PER_SESSION: str = (
    "customEvents\n"
    "| where name == \"voice.session_closed\"\n"
    "| extend session_id = tostring(customDimensions.session_id), "
    "elapsed_seconds = toint(customDimensions.elapsed_seconds)\n"
    "| summarize minutes_per_session = avg(elapsed_seconds) / 60.0 "
    "by bin(timestamp, 1h)\n"
    "| order by timestamp desc"
)

# Foundry model token usage. The MAF SDK emits a `model.tokens` metric
# (input / output tokens per turn); the workbook sums by `model`.
MODEL_TOKENS_BY_MODEL: str = (
    "customMetrics\n"
    "| where name == \"model.tokens\"\n"
    "| extend model = tostring(customDimensions.model), "
    "direction = tostring(customDimensions.direction)\n"
    "| summarize tokens = sum(value) by bin(timestamp, 1h), model, direction"
)

# Cosmos RU/s consumption. The Azure Monitor Cosmos resource provider
# emits `TotalRequestUnits`; the workbook joins by container.
COSMOS_RU_BY_CONTAINER: str = (
    "AzureMetrics\n"
    "| where ResourceProvider == \"MICROSOFT.DOCUMENTDB\"\n"
    "| where MetricName == \"TotalRequestUnits\"\n"
    "| extend container = tostring(parse_url(ResourceId).Path)\n"
    "| summarize ru = sum(Total) by bin(TimeGenerated, 1h), container"
)

# AI Search Search Units (search instance + replica count). The
# resource provider emits `SearchUnits`.
SEARCH_UNITS_OVER_TIME: str = (
    "AzureMetrics\n"
    "| where ResourceProvider == \"MICROSOFT.SEARCH\"\n"
    "| where MetricName == \"SearchUnits\"\n"
    "| summarize search_units = avg(Total) by bin(TimeGenerated, 1h)"
)


COST_QUERIES: dict[str, str] = {
    "realtime_minutes_per_session": REALTIME_MINUTES_PER_SESSION,
    "model_tokens_by_model": MODEL_TOKENS_BY_MODEL,
    "cosmos_ru_by_container": COSMOS_RU_BY_CONTAINER,
    "search_units_over_time": SEARCH_UNITS_OVER_TIME,
}


__all__ = [
    "COST_QUERIES",
    "COSMOS_RU_BY_CONTAINER",
    "MODEL_TOKENS_BY_MODEL",
    "REALTIME_MINUTES_PER_SESSION",
    "SEARCH_UNITS_OVER_TIME",
]
