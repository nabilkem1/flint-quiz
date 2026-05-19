"""Pre-public exposure gate dry-run (TASK-209).

Asserts the **gate's structural contracts** rather than the full
substantive checklist (which is the prod-env release artifact):

  * `docs/pre-public-gate.md` parses cleanly and contains all four
    mandatory sections from the spec.
  * The gate script enumerates every `- [ ]` item it finds — the
    parser is the load-bearing thing here, not the content.
  * The Makefile + CI workflow both invoke the script with the
    correct checklist path and env name.

The substantive content of the checklist (APIM live, retention
applied, LLM-boundary reviewed) is reviewed by Security + Release as
a manual signoff; this test pins the structural surface.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKLIST = REPO_ROOT / "docs" / "pre-public-gate.md"


def test_checklist_file_present_and_non_empty() -> None:
    assert CHECKLIST.exists(), "docs/pre-public-gate.md MUST exist"
    assert CHECKLIST.stat().st_size > 0


def test_checklist_declares_required_sections() -> None:
    content = CHECKLIST.read_text(encoding="utf-8")
    for required_section in (
        "Security — boundary",
        "Security — identity",
        "Security — rate limiting",
        "Retention",
        "Per-language quality",
        "Observability",
        "Disaster recovery",
    ):
        assert required_section in content, (
            f"docs/pre-public-gate.md missing required section heading "
            f"containing {required_section!r}"
        )


def test_gate_script_parses_checklist_to_a_nonempty_item_list() -> None:
    """The parser MUST find ≥ 1 check-list item; an empty result would
    silently pass the gate."""

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        from pre_public_gate import parse_gate  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    items = parse_gate(CHECKLIST)
    assert len(items) >= 10, f"only {len(items)} check-list items parsed — parser regression?"


def test_release_gate_workflow_wires_the_script_with_correct_path() -> None:
    workflow = REPO_ROOT / ".github" / "workflows" / "release-gate.yml"
    content = workflow.read_text(encoding="utf-8")
    assert "tools/pre_public_gate.py" in content
    assert "docs/pre-public-gate.md" in content


def test_makefile_invokes_the_script_with_env_argument() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "tools/pre_public_gate.py" in makefile
    assert "--checklist docs/pre-public-gate.md" in makefile
    assert "--env $(ENV)" in makefile


def test_checklist_carries_the_three_mandatory_items_per_spec() -> None:
    """`tasks/010 TASK-209` mandates three specific items must be
    asserted by the gate: APIM, retention, LLM-boundary review.
    This test asserts the words appear in the checklist."""

    content = CHECKLIST.read_text(encoding="utf-8")
    # APIM
    assert "APIM" in content or "API Management" in content
    # Retention
    assert "retention" in content.lower()
    # LLM-boundary review
    assert "llm-boundary.md" in content.lower() or "LLM-boundary" in content
