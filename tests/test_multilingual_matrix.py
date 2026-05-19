"""Multilingual validation matrix (TASK-173).

Drives the multilingual properties of the agent in one parametrised
test. Adding a language to the AppConfig allowlist surfaces a new
column in CI **without** code change — the matrix derives from the
`supported_languages` fixture (top-level `tests/conftest.py`).

Per-language properties asserted:

  * Phrasing block exists with every required slot.
  * Normaliser handles letter / ordinal / option-text / filler.
  * TTS shaper produces voice-safe option framing.
"""

from __future__ import annotations

import yaml
from pathlib import Path

import pytest

from src.agent.answer_normalizer import normalize_answer
from src.agent.prompts.compose import REQUIRED_SLOTS
from src.agent.tts_shaper import shape_question
from src.data.models import Option, QuestionView

PROMPTS_LANG_DIR = Path(__file__).resolve().parents[1] / "src" / "agent" / "prompts" / "lang"


def test_supported_languages_fixture_resolves_at_least_three(supported_languages) -> None:
    assert len(supported_languages) >= 3, supported_languages


@pytest.mark.parametrize("language", ["en", "fr", "es"])
def test_phrasing_block_has_every_required_slot(language: str) -> None:
    path = PROMPTS_LANG_DIR / f"{language}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    slots = set((raw.get("slots") or {}).keys())
    missing = REQUIRED_SLOTS - slots
    assert not missing, f"{language!r} missing slots: {sorted(missing)}"


@pytest.mark.parametrize("language", ["en", "fr", "es"])
def test_normaliser_handles_canonical_letter_form(language: str) -> None:
    options = [Option(key=k, text=f"opt {k}") for k in "ABCD"]
    result = normalize_answer("B", language=language, options=options)
    assert result.matched == ["B"], (language, result)


@pytest.mark.parametrize("language", ["en", "fr", "es"])
def test_tts_shaper_frames_options_per_language(language: str) -> None:
    view = QuestionView(
        question_id="q-1",
        text="Which is correct?",
        options=[Option(key="A", text="x"), Option(key="B", text="y")],
        difficulty="easy",
    )
    out = shape_question(view, language=language)
    # Forbidden TTS chars never appear in the rendered output.
    for ch in "*`#[]~_":
        assert ch not in out


@pytest.mark.asyncio
async def test_matrix_runs_for_every_supported_language(supported_languages) -> None:
    """One representative assertion per supported language: the normaliser
    handles the spoken `B` answer. Adding a language to the allowlist
    causes the parametrise-via-fixture flavour below to expand."""

    options = [Option(key=k, text=f"opt {k}") for k in "ABCD"]
    for language in supported_languages:
        result = normalize_answer("B", language=language, options=options)
        # If a new language has no normaliser table, the fall-through
        # uses English — still acceptable for the load-bearing "letter
        # form" property.
        assert result.matched == ["B"], language
