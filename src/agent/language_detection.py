"""First-message language detection helper (TASK-064 / FR-010, FR-011, FR-014).

The strong path is the model itself: on the first turn of a brand-new
session for a user with no recorded preference, the contract layer
instructs the model to infer the language and call
`set_language(user_id, lang)` with an ISO 639-1 code from the SEC-010
allowlist. This module provides a small **server-side** belt-and-braces
classifier for two cases the model path does not cover well:

1. **Pre-`set_language` routing.** Before the model has had a chance to
   respond, the channel layer needs to pick a phrasing block for the
   greeting on a brand-new session with no `users.preferredLanguage`.
   `detect_language(first_message)` provides a low-cost guess so the
   greeting comes out in roughly the right language; the model still
   produces the authoritative `set_language` call on the same turn.

2. **Sanity-check on the model's claim.** If the model proposes a
   language the cheap classifier disagrees with strongly, the channel
   layer can prompt for explicit confirmation rather than persisting a
   misdetection.

The detector is deliberately small and deterministic. The supported set
mirrors `data.models.SUPPORTED_LANGUAGES` — adding a new language adds a
phrasing block (TASK-062) AND extends the marker tables here.

Detection signal: count language-specific stopwords and accented
diacritics in the (lowercased) utterance. The result is one of:

  * a high-confidence ISO 639-1 code (≥ 2 markers and a clear plurality),
  * `None` (ambiguous / too short — defer to model inference, default `en`
    if the model also fails per 009-gov §3.2 GOV-021 step 3).

`detect_language` does not raise on garbage input. It returns `None`.
This is a guard against the model context, not an enforcement point —
SEC-010 allowlisting still happens in `set_language` (005-tools).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.data.models import SUPPORTED_LANGUAGES

# Token splitter. Letters + apostrophe (for French elisions like `l'azure`).
# Numbers are excluded; they carry no language signal.
_WORD_RE = re.compile(r"[a-zàâäæçéèêëîïôöœùûüÿñá-úß']+", re.IGNORECASE)

# Per-language stopwords. Picked to be common, short, and unlikely to
# appear in another language's text. Each set is a frozenset for cheap
# membership tests. The intent is "first message" detection — quizzes
# rarely arrive in 30-word utterances, so we keep the marker tables
# small and disjoint.
_MARKERS: dict[str, frozenset[str]] = {
    "en": frozenset({
        "the", "and", "what", "which", "please", "start", "begin", "topic",
        "language", "english", "quiz", "could", "would", "you", "i", "yes", "no",
    }),
    "fr": frozenset({
        "le", "la", "les", "un", "une", "des", "et", "ou", "est", "quel",
        "quelle", "quels", "quelles", "français", "francais", "bonjour",
        "salut", "merci", "veuillez", "voudrais", "voudriez", "commencer",
        "commence", "thème", "theme", "sujet", "langue", "oui", "non", "je",
    }),
    "es": frozenset({
        "el", "la", "los", "las", "un", "una", "y", "o", "es", "qué", "que",
        "cuál", "cual", "español", "espanol", "hola", "buenos", "gracias",
        "por", "favor", "empezar", "comenzar", "comienza", "tema", "idioma",
        "sí", "si", "no", "yo",
    }),
}

# Cross-check: a few unambiguous accented characters. A French utterance
# without any Latin stopwords ("Merci !") still carries enough signal via
# the diacritic.
_DIACRITIC_BIAS: dict[str, frozenset[str]] = {
    "fr": frozenset({"ç", "œ", "é", "è", "ê", "ë", "à", "â", "ï", "î", "ô", "ù", "û"}),
    "es": frozenset({"ñ", "¿", "¡", "á", "í", "ó", "ú", "ü"}),
}

# `MIN_SCORE` and `MIN_MARGIN` together define "high confidence". A single
# stopword in a five-word utterance is not enough; the leading language
# must beat the runner-up by at least one full marker.
_MIN_SCORE: int = 2
_MIN_MARGIN: int = 1


@dataclass(frozen=True, slots=True)
class LanguageGuess:
    """Result of `detect_language`.

    `code` is `None` when the utterance is too short or ambiguous.
    `scores` exposes the per-language marker counts so the caller can
    decide whether to ask the user to confirm rather than guess.
    `confidence` is a coarse 0..1 in the high-confidence case; the
    channel layer uses it only for the confirmation-prompt threshold.
    """

    code: str | None
    scores: dict[str, int]
    confidence: float


def detect_language(utterance: str) -> LanguageGuess:
    """Cheap, deterministic ISO 639-1 guess from an arbitrary user string.

    Returns `LanguageGuess(code=None, …)` on ambiguity — caller should
    either ask the user (preferred) or default to `en` per
    [009-gov §3.2 GOV-021](../../specs/009-agent-governance.md).
    """

    if not utterance:
        return LanguageGuess(code=None, scores={lang: 0 for lang in SUPPORTED_LANGUAGES}, confidence=0.0)

    lowered = utterance.lower()
    tokens = set(_WORD_RE.findall(lowered))
    diacritics = set(lowered)

    scores: dict[str, int] = {}
    for lang in SUPPORTED_LANGUAGES:
        markers = _MARKERS.get(lang, frozenset())
        bias = _DIACRITIC_BIAS.get(lang, frozenset())
        scores[lang] = len(tokens & markers) + (1 if diacritics & bias else 0)

    # Sort by score descending. Ties are broken alphabetically — a
    # deterministic-but-conservative choice; ties always trigger ambiguity
    # via the margin check below.
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    leader, leader_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else 0
    margin = leader_score - runner_score

    if leader_score < _MIN_SCORE or margin < _MIN_MARGIN:
        return LanguageGuess(code=None, scores=scores, confidence=0.0)

    # Confidence: capped at 1.0; rough scale matches "1 → barely past the
    # threshold", "≥ 4 markers ahead → definitive".
    confidence = min(1.0, 0.5 + 0.125 * margin)
    return LanguageGuess(code=leader, scores=scores, confidence=confidence)


__all__ = ["LanguageGuess", "detect_language"]
