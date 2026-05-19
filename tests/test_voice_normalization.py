"""Voice-channel normalization tests (TASK-174 / 008-api §5).

Spoken-style inputs the normaliser must handle without a separate voice
code path. The contract: the same `answer_normalizer.normalize_answer`
handles both text and voice surfaces; filler-strip + ordinal +
option-text suffices.

Examples called out in `tasks/006-voice-realtime.md §TASK-104`:

  * "Uh, letter B" → `B` (en).
  * "Je crois que c'est la deuxième" → `B` (fr).
  * Spanish "la verde" with no option matching → `None` and re-prompt.
"""

from __future__ import annotations

import pytest

from src.agent.answer_normalizer import normalize_answer
from src.data.models import Option

OPTIONS_FR = [
    Option(key="A", text="Passerelle VPN"),
    Option(key="B", text="Passerelle d'application"),
    Option(key="C", text="Pare-feu Azure"),
    Option(key="D", text="Front Door"),
]
OPTIONS_ES = [
    Option(key="A", text="Puerta de enlace VPN"),
    Option(key="B", text="Puerta de aplicaciones"),
    Option(key="C", text="Firewall de Azure"),
    Option(key="D", text="Front Door"),
]
OPTIONS_EN = [
    Option(key="A", text="VPN Gateway"),
    Option(key="B", text="Application Gateway"),
    Option(key="C", text="Azure Firewall"),
    Option(key="D", text="Front Door"),
]


@pytest.mark.parametrize(
    "raw,language,expected",
    [
        ("Uh, letter B", "en", ["B"]),
        ("um, the second", "en", ["B"]),
        ("Je crois que c'est la deuxième", "fr", ["B"]),
        ("euh, lettre B", "fr", ["B"]),
        ("este, letra B", "es", ["B"]),
        ("la segunda", "es", ["B"]),
    ],
)
def test_voice_style_inputs_normalise(raw: str, language: str, expected: list[str]) -> None:
    options = {"en": OPTIONS_EN, "fr": OPTIONS_FR, "es": OPTIONS_ES}[language]
    result = normalize_answer(raw, language=language, options=options)
    assert result.matched == expected, (raw, result)


def test_voice_style_no_match_returns_none() -> None:
    result = normalize_answer("la verde", language="es", options=OPTIONS_ES)
    assert result.matched is None
    assert result.strategy == "no_match"


@pytest.mark.parametrize("language", ["en", "fr", "es"])
def test_voice_style_chained_fillers_dont_break_matcher(language: str) -> None:
    """Multiple voice fillers in sequence don't poison the match."""

    options = {"en": OPTIONS_EN, "fr": OPTIONS_FR, "es": OPTIONS_ES}[language]
    inputs = {
        "en": "um, uh, well, letter B",
        "fr": "euh, ben, lettre B",
        "es": "este, pues, letra B",
    }[language]
    result = normalize_answer(inputs, language=language, options=options)
    assert result.matched == ["B"], (language, result)
