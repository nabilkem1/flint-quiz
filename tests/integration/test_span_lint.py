"""Span attribute lint (TASK-144 / SEC-001 in telemetry).

The build MUST fail if any source file under `src/` uses a forbidden
span-attribute key (`correct_answer`, `expected`, `_etag`, ...). The
runtime guard (`enforce_span_attributes`) covers dynamic attribute
maps; this test covers the **source-level** invariant by walking
every Python module and scanning for offending `set_attribute(...)`
/ `set_attributes({...})` patterns.

Companion: the policy is enforced at emit-time in
`src/observability/events.py` (events) and at attribute-set time in
`src/observability/spans.py` (spans).
"""

from __future__ import annotations

import pathlib

import pytest

from src.observability.spans import (
    FORBIDDEN_SPAN_ATTRS,
    SpanAttributesPolicyError,
    enforce_span_attributes,
    scan_source_for_forbidden_attrs,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"


def test_no_forbidden_attribute_in_source_tree() -> None:
    """No source file may name a forbidden span attribute."""

    offenders = scan_source_for_forbidden_attrs([SRC_ROOT])
    assert not offenders, "\n".join(
        f"{o.path.relative_to(REPO_ROOT)}:{o.line}  {o.attribute!r}  ← {o.snippet}"
        for o in offenders
    )


def test_synthetic_offending_module_is_caught(tmp_path: pathlib.Path) -> None:
    """A synthetic module that *would* leak — the scanner catches it.

    Negative test for the scanner itself; without it a refactor that
    breaks the AST walk could go unnoticed.
    """

    bad = tmp_path / "bad_module.py"
    bad.write_text(
        "def f(span):\n"
        "    span.set_attribute(\"correct_answer\", [\"B\"])\n"
        "    span.set_attributes({\"expected\": [\"A\"], \"language\": \"en\"})\n",
        encoding="utf-8",
    )
    offenders = scan_source_for_forbidden_attrs([tmp_path])
    found = {o.attribute for o in offenders}
    assert "correct_answer" in found
    assert "expected" in found


def test_enforce_span_attributes_raises_on_forbidden_key() -> None:
    with pytest.raises(SpanAttributesPolicyError):
        enforce_span_attributes({"language": "en", "correct_answer": "B"})


def test_enforce_span_attributes_passes_on_clean_attrs() -> None:
    enforce_span_attributes(
        {"language": "en", "channel": "voice", "verdict": "correct"}
    )  # no exception


def test_forbidden_attrs_constant_is_a_superset_of_minimum_required() -> None:
    """Sanity — the policy must at minimum forbid the four canonical leak
    paths called out by the SEC-001 contract."""

    minimum = {"correct_answer", "correctAnswer", "answer_key", "expected"}
    assert minimum.issubset(FORBIDDEN_SPAN_ATTRS)
