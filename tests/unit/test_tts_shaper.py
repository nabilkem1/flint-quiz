"""TTS-friendly shaper tests (TASK-087 / NFR-014 / TEST-024).

Asserts the voice-channel invariants:

  * No markdown characters (`*`, `**`, `` ` ``, `#`, `[`, `]`, `~`, `_`)
    appear in any shaped string.
  * Option keys are framed per language (`Option A:`, `Réponse A:`,
    `Opción A:`).
  * Numerals 0..20 are spelled out per language.
  * Raw URLs are replaced by a phonetic-safe spoken form.

These properties protect the voice surface; the shaper does **not** carry
SEC-001 load — the typed boundary does.
"""

from __future__ import annotations

import pytest

from src.agent.tts_shaper import (
    shape_question,
    shape_results_summary,
    shape_text,
    shape_topic_list,
    shape_verdict,
)
from src.data.models import Option, QuestionView

FORBIDDEN_MARKDOWN = "*`#[]~_"


@pytest.mark.parametrize(
    "raw",
    [
        "Hello **world**",
        "Click `here`",
        "## Heading",
        "List: [link](http://example.com)",
        "~~strike~~",
        "_emphasis_",
    ],
)
def test_shape_text_strips_markdown(raw: str) -> None:
    out = shape_text(raw, language="en")
    for ch in FORBIDDEN_MARKDOWN:
        assert ch not in out, (raw, out)


def test_shape_text_spells_small_numerals_en() -> None:
    out = shape_text("There are 5 questions and 10 minutes.", language="en")
    assert "five" in out
    assert "ten" in out
    assert " 5 " not in f" {out} "
    assert " 10 " not in f" {out} "


def test_shape_text_spells_small_numerals_fr() -> None:
    out = shape_text("Il reste 3 questions.", language="fr")
    assert "trois" in out


def test_shape_text_spells_small_numerals_es() -> None:
    out = shape_text("Quedan 3 preguntas.", language="es")
    assert "tres" in out


def test_shape_text_replaces_urls_phonetically() -> None:
    out = shape_text("See https://example.com/api for details.", language="en")
    assert "http" not in out
    assert "://" not in out
    assert "example" in out
    assert "dot" in out


def test_shape_text_idempotent() -> None:
    raw = "Hello **world** — see 5 things at https://example.com"
    once = shape_text(raw, language="en")
    twice = shape_text(once, language="en")
    assert once == twice


@pytest.mark.parametrize(
    "language,frame_prefix",
    [
        ("en", "Option A:"),
        ("fr", "Réponse A:"),
        ("es", "Opción A:"),
    ],
)
def test_shape_question_frames_options_per_language(
    language: str, frame_prefix: str
) -> None:
    qv = QuestionView(
        question_id="q-1",
        text="Which is the right answer?",
        options=[
            Option(key="A", text="VPN Gateway"),
            Option(key="B", text="Front Door"),
        ],
        difficulty="medium",
    )
    out = shape_question(qv, language=language)
    assert frame_prefix in out
    for ch in FORBIDDEN_MARKDOWN:
        assert ch not in out


def test_shape_results_summary_voice_safe() -> None:
    out = shape_results_summary(
        score=4.0, max_score=5.0, percentage=80.0, is_pass=True, language="en"
    )
    for ch in FORBIDDEN_MARKDOWN:
        assert ch not in out
    assert "four" in out  # spelled-out numeral
    assert "percent" in out


@pytest.mark.parametrize(
    "verdict,language",
    [
        ("correct", "en"),
        ("correct", "fr"),
        ("correct", "es"),
        ("incorrect", "en"),
        ("partial", "fr"),
        ("unanswered", "es"),
    ],
)
def test_shape_verdict_no_markdown(verdict: str, language: str) -> None:
    out = shape_verdict(verdict, language=language)
    for ch in FORBIDDEN_MARKDOWN:
        assert ch not in out
    assert len(out) > 0


def test_shape_topic_list_renders_comma_separated() -> None:
    topics = [
        {"topic_id": "azure-networking", "label": "Azure Networking", "count": 12, "has_fallback": False},
        {"topic_id": "azure-storage", "label": "Azure Storage", "count": 7, "has_fallback": False},
        {"topic_id": "azure-identity", "label": "Azure Identity", "count": 0, "has_fallback": True},
    ]
    out = shape_topic_list(topics, language="en")
    assert "Azure Networking" in out
    assert "Azure Storage" in out
    assert "fallback" in out  # the rendered annotation


def test_shape_topic_list_empty_returns_empty() -> None:
    assert shape_topic_list([], language="en") == ""
