"""Per-release quality gate dry-run (TASK-208).

This test does NOT re-run the full release matrix — that would
duplicate the load-bearing tests it depends on. Instead it asserts
the **structure** of the gate so a regression in the gate's wiring
shows up before a tag fires:

  * The Makefile exposes the `pre-public-check` target.
  * The release-gate workflow exists and runs TEST-006 / TEST-011 /
    pre-public-gate steps.
  * The `tools/pre_public_gate.py` script can parse the checklist
    file and produces a non-zero exit code when items are unchecked.

The actual gate enforcement happens in CI; this test guards the
gate itself.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_makefile_exposes_pre_public_check_target() -> None:
    makefile = REPO_ROOT / "Makefile"
    assert makefile.exists(), "Makefile is the load-bearing operator surface"
    content = _read(makefile)
    assert "pre-public-check" in content
    assert "rollback" in content
    assert "deploy" in content
    assert "smoke" in content


def test_release_gate_workflow_runs_test_006_and_test_011() -> None:
    workflow = REPO_ROOT / ".github" / "workflows" / "release-gate.yml"
    assert workflow.exists(), "release-gate workflow MUST be wired"
    content = _read(workflow)
    # TEST-006 — answer leakage.
    assert "test_no_answer_leakage.py" in content
    # TEST-011 — per-language Foundry Evaluation gate.
    assert "tests/eval" in content
    # Pre-public gate is conditional on the tag name.
    assert "public-ready" in content


def test_release_gate_workflow_invokes_pre_public_script() -> None:
    workflow = REPO_ROOT / ".github" / "workflows" / "release-gate.yml"
    assert "tools/pre_public_gate.py" in _read(workflow)


def test_pre_public_script_rejects_unchecked_items() -> None:
    """A checklist file with at least one `- [ ]` line MUST fail."""

    script = REPO_ROOT / "tools" / "pre_public_gate.py"
    checklist = REPO_ROOT / "docs" / "pre-public-gate.md"
    assert script.exists() and checklist.exists()

    result = subprocess.run(
        [sys.executable, str(script), "--checklist", str(checklist), "--env", "test"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # The current checklist has unchecked items (by design — pre-public
    # is aspirational until the deploy is genuinely public-ready).
    assert result.returncode != 0
    assert "Missing checks" in result.stderr


def test_pre_public_script_passes_on_synthetic_all_checked(
    tmp_path: pathlib.Path,
) -> None:
    """Synthetic all-checked file → exit 0. Pins the success path."""

    checklist = tmp_path / "all-checked.md"
    checklist.write_text(
        "# All checked\n"
        "## 1. Section A\n"
        "- [x] item one\n"
        "- [X] item two\n"
        "## 2. Section B\n"
        "- [x] item three\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "pre_public_gate.py"),
            "--checklist",
            str(checklist),
            "--env",
            "test",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PASSED" in result.stdout


def test_release_gate_does_not_carry_force_flag() -> None:
    """A `--force` flag on the gate would defeat its purpose (FORBIDDEN
    ACTIONS). Invoking the script with `--force` MUST yield a non-zero
    exit (argparse rejects the unknown option)."""

    script = REPO_ROOT / "tools" / "pre_public_gate.py"
    checklist = REPO_ROOT / "docs" / "pre-public-gate.md"
    result = subprocess.run(
        [sys.executable, str(script), "--checklist", str(checklist), "--force"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert (
        "unrecognized" in result.stderr.lower()
        or "error" in result.stderr.lower()
    )
