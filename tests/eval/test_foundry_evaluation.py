"""Per-language Foundry Evaluation gate (TEST-011 / TASK-167).

Per-language correctness is the headline metric for an exam system,
not uptime. This gate runs the **structural** half of TEST-011 in CI:

  * Loads the per-language baseline from
    ``tests/eval/baselines/correctness-baseline.json``.
  * For each supported language, computes the smoke-time correctness
    from the in-process smoke fixtures.
  * Asserts the current correctness deviates from the baseline by no
    more than ``MAX_DEVIATION_PCT``.
  * **Per-language only blocks the affected language.** A regression
    in `fr` does NOT block a publish of `en` content; the gate is
    language-scoped on purpose so a translation fix can ship without
    re-running the full evaluation.

The **live-Foundry** flavour (running against a published index) lives
on the T6 pipeline tier and is invoked via ``azd run eval-per-lang``;
this in-process gate is the cheap CI surface that catches structural
regressions.

Re-baseline cadence: **yearly OR on model change**, with explicit
review (FORBIDDEN ACTIONS — never re-baseline silently on every model
change).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.smoke._helpers import build_smoke_deps, run_end_to_end

BASELINE_PATH = pathlib.Path(__file__).resolve().parent / "baselines" / "correctness-baseline.json"

# Tolerance in percentage points. A deviation > this fails the gate for
# the affected language only.
MAX_DEVIATION_PCT: float = 5.0


def _load_baseline() -> dict[str, float]:
    if not BASELINE_PATH.exists():
        # Initial run — return an empty baseline. The test annotates
        # this as a re-baselining moment and skips rather than fails.
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _answer_resolver(language: str):
    """Per-language answer resolver matching the smoke fixtures."""

    table = {
        "en": ["B", "option B", "letter B", "the second", "B"],
        "fr": ["B", "lettre B", "réponse B", "la deuxième", "B"],
        "es": ["B", "letra B", "opción B", "la segunda", "B"],
    }
    answers = table.get(language, ["B"] * 5)
    return lambda idx: answers[idx % len(answers)]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "fr", "es"])
async def test_per_language_correctness_within_baseline(language: str) -> None:
    """Per-language gate. A regression in any one language fails THAT
    language's case only — the other languages' tests keep passing
    independently (parametrize fans out the failures)."""

    baseline = _load_baseline()
    if language not in baseline:
        pytest.skip(
            f"no baseline for {language!r} — run "
            f"`pytest tests/eval/ -k regenerate` to set one (yearly/model-change cadence)"
        )

    deps = await build_smoke_deps(language=language)
    final = await run_end_to_end(
        deps,
        user_id="user-1",
        language=language,
        topic="azure-networking",
        n=5,
        channel="text",
        answer_resolver=_answer_resolver(language),
    )
    current_pct = final["percentage"]
    baseline_pct = baseline[language]
    deviation = abs(current_pct - baseline_pct)
    assert deviation <= MAX_DEVIATION_PCT, (
        f"per-language correctness regression for {language!r}: "
        f"baseline={baseline_pct}%, current={current_pct}%, deviation={deviation}% "
        f"(tolerance={MAX_DEVIATION_PCT}%) — re-baseline only with review "
        f"(yearly OR model change)."
    )


def test_baseline_file_is_present_or_skip_reason_is_clear() -> None:
    """Sanity check — if the baseline is missing, the parametrised tests
    above skip with an explicit re-baselining message. This standalone
    case is a meta-assertion so a missing baseline shows up in the run
    summary as a single skip rather than three opaque ones."""

    if not BASELINE_PATH.exists():
        pytest.skip(
            "no per-language baseline committed; this is expected for the very "
            "first run after a model change. Run `pytest tests/eval/ -k regenerate` "
            "to author one."
        )
    baseline = _load_baseline()
    for lang in ("en", "fr", "es"):
        assert lang in baseline, (
            f"baseline missing the {lang!r} entry. Re-baseline requires explicit review."
        )
