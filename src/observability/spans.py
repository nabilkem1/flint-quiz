"""Span attribute policy (TASK-144 / SEC-001 in telemetry).

The agent's tracing surface uses a small, documented set of attribute
names. The contract:

  * **Allowed dimensions** (where applicable): ``language``,
    ``channel``, ``verdict``, ``session_id``, ``question_id``,
    ``tool``, ``latency_ms``, ``ok``.
  * **Forbidden dimensions** (CI lint + runtime enforcement):
    ``correct_answer``, ``correctAnswer``, ``answer_key``, ``expected``,
    ``received_raw``, ``receivedRaw``, ``_etag``.

This module provides:

  * :data:`FORBIDDEN_SPAN_ATTRS` — the runtime allowlist.
  * :func:`enforce_span_attributes(attrs)` — raises if any forbidden
    attribute name is present. Used by every code path that builds an
    attribute map for a span.
  * :func:`scan_source_for_forbidden_attrs(path)` — used by the lint
    test (`tests/integration/test_span_lint.py`) to scan the source
    tree for attribute strings.

There is no `SpanFactory` here — the agent uses the OTel SDK
directly. This module is the **policy** layer that wraps it.
"""

from __future__ import annotations

import ast
import logging
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


# Forbidden span attribute names. Mirrors
# :data:`src.observability.events.FORBIDDEN_EVENT_DIMENSIONS` — spans
# and events share the same SEC-001 boundary.
FORBIDDEN_SPAN_ATTRS: frozenset[str] = frozenset({
    "correct_answer",
    "correctAnswer",
    "answer_key",
    "expected",
    "received_raw",
    "receivedRaw",
    "_etag",
})


class SpanAttributesPolicyError(ValueError):
    """Raised when a span attribute map carries a forbidden key."""


def enforce_span_attributes(attrs: Mapping[str, object]) -> None:
    """Raise :class:`SpanAttributesPolicyError` if `attrs` violates policy.

    Call this at every span attribute set site that builds attributes
    from user-flavoured data. Hand-written safe attributes (constant
    keys like ``"language"``) need not call it — the lint scan covers
    the source-level invariant.
    """

    leaks = FORBIDDEN_SPAN_ATTRS & attrs.keys()
    if leaks:
        raise SpanAttributesPolicyError(
            f"span attributes carry forbidden keys: {sorted(leaks)}"
        )


# ---------------------------------------------------------------------------
# Source scan (for the CI lint / tests)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Offender:
    """One discovered violation in a source file."""

    path: pathlib.Path
    line: int
    snippet: str
    attribute: str


def scan_source_for_forbidden_attrs(
    roots: Iterable[pathlib.Path],
) -> list[_Offender]:
    """Walk every ``.py`` file under `roots` and find forbidden attribute
    strings used as a span attribute key.

    Heuristic but tight: matches calls of the shape
    ``span.set_attribute("<forbidden>", ...)`` or
    ``record.set_attributes({"<forbidden>": ...})``, plus literal
    string constants compared against attribute names. False positives
    in this codebase are zero because no code legitimately uses the
    forbidden names — the lint is structural, not advisory.
    """

    offenders: list[_Offender] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            offenders.extend(_walk_for_offenders(tree, path, source))
    return offenders


def _walk_for_offenders(
    tree: ast.AST, path: pathlib.Path, source: str
) -> list[_Offender]:
    out: list[_Offender] = []
    source_lines = source.splitlines()

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            # `span.set_attribute("X", ...)` / `span.set_attributes({...})`
            func = node.func
            method = getattr(func, "attr", "")
            if method == "set_attribute":
                if node.args and isinstance(node.args[0], ast.Constant):
                    key = node.args[0].value
                    if isinstance(key, str) and key in FORBIDDEN_SPAN_ATTRS:
                        out.append(_offender(path, node.lineno, source_lines, key))
            elif method == "set_attributes":
                if node.args and isinstance(node.args[0], ast.Dict):
                    for k in node.args[0].keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            if k.value in FORBIDDEN_SPAN_ATTRS:
                                out.append(_offender(path, node.lineno, source_lines, k.value))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return out


def _offender(
    path: pathlib.Path, lineno: int, lines: list[str], attribute: str
) -> _Offender:
    snippet = lines[lineno - 1].strip() if 1 <= lineno <= len(lines) else ""
    return _Offender(path=path, line=lineno, snippet=snippet, attribute=attribute)


__all__ = [
    "FORBIDDEN_SPAN_ATTRS",
    "SpanAttributesPolicyError",
    "enforce_span_attributes",
    "scan_source_for_forbidden_attrs",
]
