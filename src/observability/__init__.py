"""Observability surface for the Flint Quiz agent (008-observability).

This package is the **single canonical source** of telemetry emission.
Every `grading_event`, every `agent.*` governance event, every span
created by tool code routes through these helpers so the dimension
policy is enforced centrally (008-api §0.1 / §4.5.1).

Three modules:

  * :mod:`telemetry`  — OpenTelemetry init via
    ``azure-monitor-opentelemetry``; Foundry tracing enable.
  * :mod:`events`     — typed event emitters. The :class:`AgentEvent`
    taxonomy mirrors 008-observability TASK-149.
  * :mod:`spans`      — span attribute helpers + the forbidden-attribute
    lint reused by tests and CI.
  * :mod:`cost`       — small set of queries the cost workbook reads.

The package never imports tool / dispatcher / repository modules — the
opposite arrow is fine. This keeps the layering clean for
``import-linter``: ``src.observability`` may be imported by anyone,
imports almost nothing.
"""

from src.observability.events import (
    AgentEvent,
    EventEmitter,
    NullEmitter,
    RecordingEmitter,
    emit_agent_event,
    emit_grading_event,
)
from src.observability.spans import (
    FORBIDDEN_SPAN_ATTRS,
    SpanAttributesPolicyError,
    enforce_span_attributes,
)
from src.observability.telemetry import (
    TelemetryConfig,
    initialise_telemetry,
)

__all__ = [
    "AgentEvent",
    "EventEmitter",
    "FORBIDDEN_SPAN_ATTRS",
    "NullEmitter",
    "RecordingEmitter",
    "SpanAttributesPolicyError",
    "TelemetryConfig",
    "emit_agent_event",
    "emit_grading_event",
    "enforce_span_attributes",
    "initialise_telemetry",
]
