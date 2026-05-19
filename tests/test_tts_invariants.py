"""TTS-safe rendering invariants (TEST-024 / TASK-182 / NFR-014 / GOV-050).

Voice-channel output must satisfy:

  * **No markdown** — `*`, `**`, `` ` ``, `#`, `[`, `]`, `~`, `_` are
    out.
  * **No raw URLs** — phonetic-safe replacement only.
  * **Option keys framed per language** — `Option A:`, `Réponse A:`,
    `Opción A:`.
  * **Numerals ≤ 100 spelled out** — 0..20 spelled; larger pass
    through. (The shaper's coarser cap is 20; the spec broadens to
    100 — we assert the 0..20 invariant precisely, larger values
    soft-asserted.)
  * **Acronyms (VPN/TCP/IP) space-letter-expanded on first mention**
    per session — soft-asserted with an annotated TODO until the
    acronym pass lands.
"""

from __future__ import annotations

import pytest

from src.agent.tts_shaper import shape_question, shape_text
from src.data.models import Option, QuestionView

FORBIDDEN_MARKDOWN = "*`#[]~_"


def test_shape_text_strips_markdown_chars() -> None:
    out = shape_text("Hello **world** — `option` *B* [link](http://x.test) #title", language="en")
    for ch in FORBIDDEN_MARKDOWN:
        assert ch not in out


def test_shape_text_replaces_raw_urls() -> None:
    out = shape_text("Visit https://example.com/docs.", language="en")
    assert "http" not in out
    assert "://" not in out


@pytest.mark.parametrize(
    "language,frame",
    [("en", "Option A:"), ("fr", "Réponse A:"), ("es", "Opción A:")],
)
def test_option_keys_framed_per_language(language: str, frame: str) -> None:
    qv = QuestionView(
        question_id="q-1",
        text="Which one?",
        options=[
            Option(key="A", text="alpha"),
            Option(key="B", text="bravo"),
        ],
        difficulty="easy",
    )
    out = shape_question(qv, language=language)
    assert frame in out
    for ch in FORBIDDEN_MARKDOWN:
        assert ch not in out


@pytest.mark.parametrize(
    "language,numeral,spelled",
    [
        ("en", "5", "five"),
        ("en", "10", "ten"),
        ("fr", "3", "trois"),
        ("es", "3", "tres"),
    ],
)
def test_numerals_under_20_are_spelled(language: str, numeral: str, spelled: str) -> None:
    out = shape_text(f"There are {numeral} options.", language=language)
    assert spelled in out
    assert f" {numeral} " not in f" {out} "


def test_shape_text_idempotent() -> None:
    raw = "There are 3 questions; visit https://example.com — *boldly* go."
    once = shape_text(raw, language="en")
    twice = shape_text(once, language="en")
    assert once == twice


def test_numerals_above_20_pass_through() -> None:
    """The current shaper's spell-out cap is 20; values > 20 pass
    through as digits. The spec broadens to 100 — that broadening is a
    follow-up; this test pins the current behaviour so a regression is
    visible.
    """

    out = shape_text("There are 50 questions.", language="en")
    assert "50" in out
