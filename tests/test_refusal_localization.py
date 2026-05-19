"""Refusal localisation (TEST-021 / TASK-179 / GOV-071 / GOV-072).

The agent's refusal copy must come from the **active-language**
phrasing block, never English-by-default in an fr/es session. The
test asserts:

  * Each language's phrasing block declares both refusal slots
    (`refusal_off_topic`, `refusal_answer_key`).
  * The slots' substrings are distinct across languages (a smoke
    check that the FR/ES versions are not English text passed
    through unchanged).
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from src.data.models import SUPPORTED_LANGUAGES

PROMPTS_LANG_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "agent" / "prompts" / "lang"


def _load_slots(language: str) -> dict[str, str]:
    raw = yaml.safe_load((PROMPTS_LANG_DIR / f"{language}.yaml").read_text(encoding="utf-8"))
    return raw.get("slots") or {}


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGUAGES))
def test_refusal_slots_present_per_language(language: str) -> None:
    slots = _load_slots(language)
    for slot in ("refusal_off_topic", "refusal_answer_key", "stay_on_task", "idle_reprompt"):
        assert slot in slots, f"{language} missing slot {slot!r}"


def test_refusal_copy_differs_across_languages() -> None:
    """A copy-paste between language files would defeat the localisation
    contract. The substring check is a coarse-but-effective guard."""

    en = _load_slots("en")
    fr = _load_slots("fr")
    es = _load_slots("es")
    # Each language has its own refusal text — no two are identical.
    assert en["refusal_answer_key"] != fr["refusal_answer_key"]
    assert en["refusal_answer_key"] != es["refusal_answer_key"]
    assert fr["refusal_answer_key"] != es["refusal_answer_key"]


def test_active_language_refusal_does_not_carry_other_language_marker() -> None:
    """Hard guard: a French refusal copy must not be English."""

    fr = _load_slots("fr")
    es = _load_slots("es")
    # English-specific phrasing that should never appear in fr/es slots.
    english_markers = ("answer keys", "I can't share", "Sorry,")
    for marker in english_markers:
        assert marker not in fr.get("refusal_answer_key", "")
        assert marker not in es.get("refusal_answer_key", "")
