"""Multilingual answer normaliser (TASK-086 / 004-agent §6 / 008-api §5).

Converts a spoken or typed user variant into one or more `OptionKey`
letters. Deterministic, NFKD-stripped, accent-insensitive — there is no
LLM call inside the normaliser, so a jailbreak can never "rewrite" how a
verdict is grader's-side computed.

Per-language tables are kept inline for v1 (en / fr / es) because the
allowlist is small. Adding a language is a matter of:

  1. Extend `SUPPORTED_LANGUAGES` in `src/data/models.py` (SEC-010).
  2. Drop a new entry into `_LANGUAGE_TABLES` here with the position
     phrases, letter prefixes, voice fillers, and skip tokens.
  3. Drop a phrasing block under `src/agent/prompts/lang/`.
  4. The grader code does **not** change — that is the property the
     module's structure guarantees.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Sequence

from src.data.models import Option


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    """Outcome of normalising a raw answer.

    `matched` is a list of option keys (`["A"]` for single-correct,
    `["A", "C"]` for multi-correct), or `None` if no strategy succeeded.
    `strategy` is a short label exposed to telemetry — *never* to the LLM
    — so we can diagnose normaliser-class drift per language. `ambiguous`
    fires when two distinct strategies yielded conflicting results; the
    tool layer surfaces `E_NORMALIZER_AMBIGUOUS` and re-prompts.
    """

    matched: list[str] | None
    strategy: str
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class _LanguageTable:
    """Per-language phrase data driving the normalisation passes.

    `ordinal_phrases` maps each rank (1..N) to the canonical phrase plus
    synonyms. `letter_prefixes` is the set of tokens that precede a single
    letter to indicate an option key — e.g., `option a`, `letra a`. Voice
    fillers are stripped before matching. Affirmative / negative / skip
    sets feed the consent-flow + skip semantics described in 008-api §5
    and GOV-104.
    """

    ordinal_phrases: dict[int, tuple[str, ...]]
    letter_prefixes: tuple[str, ...]
    voice_fillers: tuple[str, ...]
    skip_tokens: tuple[str, ...]
    multi_joiners: tuple[str, ...] = field(default=())
    negation_tokens: tuple[str, ...] = field(default=())


# Ordinal phrases up to 8 cover the multiple-choice question max (Option model
# limits options to 8). Synonyms are deliberately conservative — adding a new
# variant is cheap; promoting a homonym would broaden the matcher in ways the
# language-resolution test would flag.
_LANGUAGE_TABLES: dict[str, _LanguageTable] = {
    "en": _LanguageTable(
        ordinal_phrases={
            1: ("first", "the first", "the first one", "1st", "number one", "number 1"),
            2: ("second", "the second", "the second one", "2nd", "number two", "number 2"),
            3: ("third", "the third", "the third one", "3rd", "number three", "number 3"),
            4: ("fourth", "the fourth", "the fourth one", "4th", "number four", "number 4"),
            5: ("fifth", "the fifth", "the fifth one", "5th", "number five", "number 5"),
            6: ("sixth", "the sixth", "the sixth one", "6th", "number six", "number 6"),
            7: ("seventh", "the seventh", "the seventh one", "7th"),
            8: ("eighth", "the eighth", "the eighth one", "8th"),
        },
        letter_prefixes=("letter", "option", "answer", "choice"),
        voice_fillers=("um", "uh", "er", "hmm", "well", "like", "you know"),
        skip_tokens=("skip", "pass", "next", "i pass", "i'll pass"),
        multi_joiners=(" and ", " & "),
        negation_tokens=("not", "anything but", "except", "no not"),
    ),
    "fr": _LanguageTable(
        ordinal_phrases={
            1: ("premier", "premiere", "la premiere", "le premier", "1er", "1ere", "numero un"),
            2: ("deuxieme", "la deuxieme", "le deuxieme", "seconde", "second", "2eme", "numero deux"),
            3: ("troisieme", "la troisieme", "le troisieme", "3eme", "numero trois"),
            4: ("quatrieme", "la quatrieme", "le quatrieme", "4eme", "numero quatre"),
            5: ("cinquieme", "la cinquieme", "le cinquieme", "5eme", "numero cinq"),
            6: ("sixieme", "la sixieme", "le sixieme", "6eme", "numero six"),
            7: ("septieme", "la septieme", "le septieme", "7eme"),
            8: ("huitieme", "la huitieme", "le huitieme", "8eme"),
        },
        letter_prefixes=("lettre", "option", "reponse", "choix"),
        voice_fillers=("euh", "ben", "alors", "donc", "bah", "heu"),
        skip_tokens=("passer", "je passe", "suivant", "passe"),
        multi_joiners=(" et ", " & "),
        negation_tokens=("pas", "sauf", "excepte", "a part"),
    ),
    "es": _LanguageTable(
        ordinal_phrases={
            1: ("primero", "primera", "la primera", "el primero", "1ero", "numero uno"),
            2: ("segundo", "segunda", "la segunda", "el segundo", "2do", "numero dos"),
            3: ("tercero", "tercera", "la tercera", "el tercero", "3ero", "numero tres"),
            4: ("cuarto", "cuarta", "la cuarta", "el cuarto", "4to", "numero cuatro"),
            5: ("quinto", "quinta", "la quinta", "el quinto", "5to", "numero cinco"),
            6: ("sexto", "sexta", "la sexta", "el sexto", "6to", "numero seis"),
            7: ("septimo", "septima", "la septima", "el septimo", "7mo"),
            8: ("octavo", "octava", "la octava", "el octavo", "8vo"),
        },
        letter_prefixes=("letra", "opcion", "respuesta", "eleccion"),
        voice_fillers=("este", "esto", "pues", "bueno", "eh", "mmm"),
        skip_tokens=("saltar", "paso", "siguiente", "me salto"),
        multi_joiners=(" y ", " & "),
        negation_tokens=("no", "salvo", "excepto", "menos"),
    ),
}


_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s'-]+", flags=re.UNICODE)


def _strip_accents(value: str) -> str:
    """NFKD decompose and drop combining marks. ñ stays as `n`; this is the
    standard normalization used by the question-search analyzers."""

    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _canonical(value: str) -> str:
    """Lowercase, strip accents, collapse whitespace, drop punctuation."""

    folded = _strip_accents(value).lower()
    folded = _PUNCT_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", folded).strip()


def _strip_fillers(value: str, fillers: Sequence[str]) -> str:
    """Remove voice fillers that bracket the real signal — "um, B" → "B"."""

    out = value
    for filler in fillers:
        if not filler:
            continue
        pattern = rf"\b{re.escape(filler)}\b"
        out = re.sub(pattern, " ", out)
    return _WHITESPACE_RE.sub(" ", out).strip()


def normalize_answer(
    raw_answer: str,
    *,
    language: str,
    options: Sequence[Option],
    accept_multi: bool = False,
) -> NormalizeResult:
    """Normalise `raw_answer` into one or more `OptionKey` letters.

    Strategy order (008-api §5.2):

      1. `skip`           — explicit skip; returns `matched=None`, `strategy="skip"`.
      2. `negation_reject` — "not A" / "sauf B" → `matched=None`.
      3. `key`            — direct letter / `option a` / `letra a`.
      4. `ordinal`        — "the first" / "la première" / "la primera".
      5. `option_text`    — case- and accent-insensitive match on option text.
      6. else             — `matched=None` so the tool re-prompts politely.
    """

    table = _LANGUAGE_TABLES.get(language)
    if table is None:
        # No locale table → fall back to the lowest-common-denominator
        # passes (letter-form match, option-text match). Unsupported codes
        # are rejected upstream (SEC-010); this is the belt-and-braces
        # behaviour for tests that synthesise locales.
        table = _LANGUAGE_TABLES["en"]

    if not raw_answer or not raw_answer.strip():
        return NormalizeResult(matched=None, strategy="empty")

    canonical = _canonical(raw_answer)
    stripped = _strip_fillers(canonical, table.voice_fillers)
    if not stripped:
        return NormalizeResult(matched=None, strategy="empty")

    option_keys = [opt.key for opt in options]
    keyset = {key.upper() for key in option_keys}

    # ---- 1. Skip token (008-api §5.2.6, GOV-104) ---------------------------
    if stripped in {_canonical(tok) for tok in table.skip_tokens}:
        return NormalizeResult(matched=None, strategy="skip")

    # ---- 2. Negation reject (008-api §5.2.5) ------------------------------
    if _contains_negation(stripped, table.negation_tokens):
        return NormalizeResult(matched=None, strategy="negation_reject")

    # ---- 3. Key / letter form ---------------------------------------------
    direct_keys = _match_letter_form(stripped, keyset, table)
    if direct_keys:
        if not accept_multi and len(direct_keys) > 1:
            return NormalizeResult(matched=None, strategy="key", ambiguous=True)
        return NormalizeResult(matched=direct_keys, strategy="key")

    # ---- 4. Ordinal --------------------------------------------------------
    ordinal_keys = _match_ordinal(stripped, options, table)
    if ordinal_keys:
        if not accept_multi and len(ordinal_keys) > 1:
            return NormalizeResult(matched=None, strategy="ordinal", ambiguous=True)
        return NormalizeResult(matched=ordinal_keys, strategy="ordinal")

    # ---- 5. Option text ----------------------------------------------------
    text_keys = _match_option_text(stripped, options)
    if text_keys:
        if not accept_multi and len(text_keys) > 1:
            return NormalizeResult(matched=None, strategy="option_text", ambiguous=True)
        return NormalizeResult(matched=text_keys, strategy="option_text")

    return NormalizeResult(matched=None, strategy="no_match")


# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------


def _contains_negation(stripped: str, negations: Sequence[str]) -> bool:
    """Detect a leading negation token. `"not a"` → True. `"not the green one"`
    is also True — the explicit negation always wins; we never grade it."""

    if not negations:
        return False
    tokens = stripped.split()
    if not tokens:
        return False
    canonical_negations = {_canonical(n) for n in negations}
    if tokens[0] in canonical_negations:
        return True
    # Multi-word negations ("anything but B") — match by phrase prefix.
    for phrase in canonical_negations:
        if " " in phrase and stripped.startswith(phrase + " "):
            return True
    return False


def _match_letter_form(
    stripped: str,
    keyset: set[str],
    table: _LanguageTable,
) -> list[str] | None:
    """Match `B`, `option b`, `letter b`, `letra b`, `lettre b`."""

    # 1. Single bare letter — case-insensitive against option set.
    upper = stripped.upper()
    if len(upper) == 1 and upper in keyset:
        return [upper]

    # 2. Prefix forms: "option a", "letter a" / "letra a" / "lettre a" / "réponse a".
    canonical_prefixes = [_canonical(p) for p in table.letter_prefixes]
    for prefix in canonical_prefixes:
        # `<prefix> X` (single letter) or `<prefix> X Y` etc. for multi.
        token = f"{prefix} "
        if stripped.startswith(token):
            candidate = stripped[len(token) :].strip()
            keys = _collect_letter_run(candidate, keyset, table)
            if keys:
                return keys

    # 3. Multi: "A and C" / "A et C" / "A y C".
    keys = _collect_letter_run(stripped, keyset, table)
    if keys and len(keys) > 1:
        return keys

    return None


def _collect_letter_run(
    candidate: str,
    keyset: set[str],
    table: _LanguageTable,
) -> list[str] | None:
    """Pull `A`, `A and B`, `A et B`, `A y B` style letter lists."""

    normalised = candidate
    for joiner in table.multi_joiners:
        normalised = normalised.replace(joiner, " ")
    normalised = _WHITESPACE_RE.sub(" ", normalised).strip()
    if not normalised:
        return None
    parts = [p.strip().upper() for p in normalised.split()]
    if not all(len(p) == 1 and p in keyset for p in parts):
        return None
    # Preserve order, drop duplicates so a stutter ("A A") doesn't double.
    seen: set[str] = set()
    keys: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            keys.append(p)
    return keys


def _match_ordinal(
    stripped: str,
    options: Sequence[Option],
    table: _LanguageTable,
) -> list[str] | None:
    """Match "the first" / "la première" / "la primera" to a position-indexed key."""

    max_rank = len(options)
    matches: list[str] = []
    for rank, phrases in table.ordinal_phrases.items():
        if rank > max_rank:
            continue
        canonical_phrases = {_canonical(p) for p in phrases}
        if stripped in canonical_phrases or any(
            stripped == p or _contains_phrase(stripped, p) for p in canonical_phrases
        ):
            key = options[rank - 1].key.upper()
            if key not in matches:
                matches.append(key)
    return matches or None


def _contains_phrase(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return haystack == needle or haystack.startswith(needle + " ") or haystack.endswith(" " + needle)


def _match_option_text(stripped: str, options: Sequence[Option]) -> list[str] | None:
    """Match the canonical option text exactly (case- and accent-insensitive)."""

    matches: list[str] = []
    for opt in options:
        canonical_text = _canonical(opt.text)
        if not canonical_text:
            continue
        if stripped == canonical_text or canonical_text in stripped:
            key = opt.key.upper()
            if key not in matches:
                matches.append(key)
    return matches or None


__all__ = ["NormalizeResult", "normalize_answer"]
