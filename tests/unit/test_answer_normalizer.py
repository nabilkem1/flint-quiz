"""Multilingual answer-normaliser tests (TASK-086 / 008-api §5).

Parametrised across `en` / `fr` / `es` with ≥10 variants per language.
The matcher must be deterministic, accent-insensitive (NFKD strip), and
must distinguish:

  * `key`         — direct letter / `option a` / `letra a` / `lettre a`.
  * `ordinal`     — "the first" / "la première" / "la primera".
  * `option_text` — match against the option's `text` field.
  * `skip`        — explicit skip intent (`"skip"` / `"je passe"` / `"paso"`).
  * `negation_reject` — "not A" / "sauf B" — agent re-prompts.
  * `no_match`    — none of the above — returns `matched=None`.

The strategy label is exposed for telemetry — the test asserts it so
class-level regressions (e.g., a letter slipping into the `ordinal` lane)
are visible immediately.
"""

from __future__ import annotations

import pytest

from src.agent.answer_normalizer import NormalizeResult, normalize_answer
from src.data.models import Option

OPTIONS_4 = [
    Option(key="A", text="VPN Gateway"),
    Option(key="B", text="Application Gateway"),
    Option(key="C", text="Azure Firewall"),
    Option(key="D", text="Front Door"),
]

OPTIONS_4_FR = [
    Option(key="A", text="Passerelle VPN"),
    Option(key="B", text="Passerelle d'application"),
    Option(key="C", text="Pare-feu Azure"),
    Option(key="D", text="Front Door"),
]

OPTIONS_4_ES = [
    Option(key="A", text="Puerta de enlace VPN"),
    Option(key="B", text="Puerta de aplicaciones"),
    Option(key="C", text="Firewall de Azure"),
    Option(key="D", text="Front Door"),
]


# ---------------------------------------------------------------------------
# English — 10 variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected,strategy",
    [
        ("A", ["A"], "key"),
        ("B", ["B"], "key"),
        ("option a", ["A"], "key"),
        ("letter B", ["B"], "key"),
        ("the first", ["A"], "ordinal"),
        ("the second one", ["B"], "ordinal"),
        ("third", ["C"], "ordinal"),
        ("um, letter B", ["B"], "key"),
        ("VPN Gateway", ["A"], "option_text"),
        ("front door", ["D"], "option_text"),
    ],
)
def test_normalizer_en(raw: str, expected: list[str], strategy: str) -> None:
    result = normalize_answer(raw, language="en", options=OPTIONS_4)
    assert result.matched == expected, (raw, result)
    assert result.strategy == strategy, (raw, result)


def test_normalizer_en_skip() -> None:
    result = normalize_answer("skip", language="en", options=OPTIONS_4)
    assert result.matched is None
    assert result.strategy == "skip"


def test_normalizer_en_negation_reject() -> None:
    result = normalize_answer("not A", language="en", options=OPTIONS_4)
    assert result.matched is None
    assert result.strategy == "negation_reject"


def test_normalizer_en_no_match() -> None:
    result = normalize_answer("the green one", language="en", options=OPTIONS_4)
    assert result.matched is None
    assert result.strategy == "no_match"


# ---------------------------------------------------------------------------
# French — 10 variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected,strategy",
    [
        ("A", ["A"], "key"),
        ("B", ["B"], "key"),
        ("option A", ["A"], "key"),
        ("lettre B", ["B"], "key"),
        ("la première", ["A"], "ordinal"),
        ("la deuxième", ["B"], "ordinal"),
        ("troisième", ["C"], "ordinal"),
        ("euh, lettre B", ["B"], "key"),
        ("Passerelle VPN", ["A"], "option_text"),
        ("réponse C", ["C"], "key"),
    ],
)
def test_normalizer_fr(raw: str, expected: list[str], strategy: str) -> None:
    result = normalize_answer(raw, language="fr", options=OPTIONS_4_FR)
    assert result.matched == expected, (raw, result)
    assert result.strategy == strategy, (raw, result)


def test_normalizer_fr_negation_reject() -> None:
    result = normalize_answer("sauf C", language="fr", options=OPTIONS_4_FR)
    assert result.matched is None
    assert result.strategy == "negation_reject"


# ---------------------------------------------------------------------------
# Spanish — 10 variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected,strategy",
    [
        ("A", ["A"], "key"),
        ("B", ["B"], "key"),
        ("opción A", ["A"], "key"),
        ("letra B", ["B"], "key"),
        ("la primera", ["A"], "ordinal"),
        ("la segunda", ["B"], "ordinal"),
        ("tercero", ["C"], "ordinal"),
        ("este, letra B", ["B"], "key"),
        ("Puerta de enlace VPN", ["A"], "option_text"),
        ("respuesta C", ["C"], "key"),
    ],
)
def test_normalizer_es(raw: str, expected: list[str], strategy: str) -> None:
    result = normalize_answer(raw, language="es", options=OPTIONS_4_ES)
    assert result.matched == expected, (raw, result)
    assert result.strategy == strategy, (raw, result)


def test_normalizer_es_skip() -> None:
    result = normalize_answer("paso", language="es", options=OPTIONS_4_ES)
    assert result.matched is None
    assert result.strategy == "skip"


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------


def test_normalizer_accent_insensitive() -> None:
    """NFKD + accent strip — `la deuxieme` (no accent) must match `la deuxième`."""

    with_accent = normalize_answer("la deuxième", language="fr", options=OPTIONS_4_FR)
    without_accent = normalize_answer("la deuxieme", language="fr", options=OPTIONS_4_FR)
    assert with_accent.matched == without_accent.matched == ["B"]


def test_normalizer_multi_correct_letters() -> None:
    """Multi-correct: `A and C` (en), `A et C` (fr), `A y C` (es)."""

    result_en = normalize_answer("A and C", language="en", options=OPTIONS_4, accept_multi=True)
    assert result_en.matched == ["A", "C"]

    result_fr = normalize_answer("A et C", language="fr", options=OPTIONS_4_FR, accept_multi=True)
    assert result_fr.matched == ["A", "C"]

    result_es = normalize_answer("A y C", language="es", options=OPTIONS_4_ES, accept_multi=True)
    assert result_es.matched == ["A", "C"]


def test_normalizer_empty_input_returns_none() -> None:
    result = normalize_answer("", language="en", options=OPTIONS_4)
    assert result.matched is None
    assert result.strategy == "empty"


def test_normalizer_result_is_dataclass() -> None:
    result = normalize_answer("A", language="en", options=OPTIONS_4)
    assert isinstance(result, NormalizeResult)
    assert result.ambiguous is False


# ---------------------------------------------------------------------------
# Voice-channel cases (TASK-104) — STT-style outputs the normaliser must
# handle without a separate voice code path. The agent passes the raw
# transcript through; the normaliser strips fillers + ordinal preambles.
# ---------------------------------------------------------------------------


def test_normalizer_voice_fr_sentence_preamble_resolves() -> None:
    """`"Je crois que c'est la deuxième"` → `B` — ordinal suffix match."""

    result = normalize_answer(
        "Je crois que c'est la deuxième",
        language="fr",
        options=OPTIONS_4_FR,
    )
    assert result.matched == ["B"]
    assert result.strategy == "ordinal"


def test_normalizer_voice_es_no_match_for_misheard_option_text() -> None:
    """Spanish `"la verde"` has no matching option — agent re-prompts."""

    result = normalize_answer("la verde", language="es", options=OPTIONS_4_ES)
    assert result.matched is None
    assert result.strategy == "no_match"


def test_normalizer_voice_filler_runs_are_stripped() -> None:
    """Multiple voice fillers in one utterance don't break the matcher."""

    assert normalize_answer(
        "um, uh, well, letter B", language="en", options=OPTIONS_4
    ).matched == ["B"]
    assert normalize_answer(
        "euh, ben, lettre B", language="fr", options=OPTIONS_4_FR
    ).matched == ["B"]
    assert normalize_answer(
        "este, pues, letra B", language="es", options=OPTIONS_4_ES
    ).matched == ["B"]
