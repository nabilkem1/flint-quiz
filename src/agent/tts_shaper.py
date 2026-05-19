"""TTS-friendly response shaper (TASK-087 / NFR-014).

Tool returns and agent messages must be safe to read aloud. The shaper:

  * Strips markdown / code-fence characters (`*`, `**`, `` ` ``, `#`,
    `[`, `]`, `~`, `_`).
  * Frames option keys per language — `Option A: …`, `Réponse A: …`,
    `Opción A: …` — so a voice channel doesn't pronounce "A:" as
    punctuation.
  * Spells small integers (0..20) per language so TTS doesn't read
    them as digits-by-digit.
  * Replaces raw URLs with a phonetic-safe spoken form ("link at
    example dot com").

The shaper is **deterministic** and stateless. It is not a security
boundary; the defensive strip (`defensive_strip.py`) and the typed
models (`QuestionView`, `_ToolModel.extra=forbid`) carry the SEC-001
load. The shaper's job is purely surface-level rendering so the agent
and the voice channel see strings that TTS won't mangle.
"""

from __future__ import annotations

import re
from typing import Mapping, Sequence

from src.data.models import Option, QuestionView

# Markdown / code-fence characters that have no business in a TTS surface.
# Leaving them in produces literal spoken-aloud "asterisk" / "backtick"
# noise on the voice channel.
_FORBIDDEN_MARKDOWN_RE = re.compile(r"[*`#\[\]~_]+")

# Raw URLs — replace with a phonetic-safe form. Conservative pattern: only
# absolute http(s) URLs; "azure.com" in question text is left alone (the
# voice channel reads dots cleanly).
_URL_RE = re.compile(r"https?://\S+", flags=re.IGNORECASE)

# Per-language option framings (NFR-014). Adding a language = drop in an
# entry here and in `_NUMERAL_WORDS`.
_OPTION_FRAMES: dict[str, str] = {
    "en": "Option {key}: {text}.",
    "fr": "Réponse {key}: {text}.",
    "es": "Opción {key}: {text}.",
}

# Spelled-out numerals 0..20 per language. Beyond 20, numerals pass
# through verbatim — TTS engines tolerate larger numerals well, and
# rewriting dates/codes would do more harm than good.
_NUMERAL_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
        "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
        "10": "ten", "11": "eleven", "12": "twelve", "13": "thirteen",
        "14": "fourteen", "15": "fifteen", "16": "sixteen", "17": "seventeen",
        "18": "eighteen", "19": "nineteen", "20": "twenty",
    },
    "fr": {
        "0": "zéro", "1": "un", "2": "deux", "3": "trois", "4": "quatre",
        "5": "cinq", "6": "six", "7": "sept", "8": "huit", "9": "neuf",
        "10": "dix", "11": "onze", "12": "douze", "13": "treize",
        "14": "quatorze", "15": "quinze", "16": "seize", "17": "dix-sept",
        "18": "dix-huit", "19": "dix-neuf", "20": "vingt",
    },
    "es": {
        "0": "cero", "1": "uno", "2": "dos", "3": "tres", "4": "cuatro",
        "5": "cinco", "6": "seis", "7": "siete", "8": "ocho", "9": "nueve",
        "10": "diez", "11": "once", "12": "doce", "13": "trece",
        "14": "catorce", "15": "quince", "16": "dieciséis", "17": "diecisiete",
        "18": "dieciocho", "19": "diecinueve", "20": "veinte",
    },
}

# Phonetic spoken forms — one per language so the URL replacement reads
# naturally in the active language.
_URL_PHONETIC_VERB: dict[str, str] = {
    "en": "link at ",
    "fr": "lien à ",
    "es": "enlace en ",
}

# Integer-token regex used for numeral expansion. Captures only standalone
# integer tokens, not digits embedded in identifiers like `RFC1918`.
_INT_TOKEN_RE = re.compile(r"(?<![\w/])(\d{1,3})(?![\w/])")


def shape_text(text: str, *, language: str) -> str:
    """Apply the TTS-friendly pipeline to a single string.

    Order: strip markdown → replace URLs → spell small numerals → squash
    whitespace. Each pass is idempotent; running the shaper twice yields
    the same string.
    """

    if not text:
        return ""

    out = _FORBIDDEN_MARKDOWN_RE.sub("", text)
    out = _replace_urls(out, language)
    out = _spell_small_numerals(out, language)
    return re.sub(r"\s+", " ", out).strip()


def shape_question(question: QuestionView, *, language: str) -> str:
    """Render a question as a TTS-safe, single-string prompt.

    Layout:
      "<question text>. Option A: <text>. Option B: <text>. … <end-prompt>."
    The end-prompt is omitted in this surface — the agent's phrasing block
    (`frame_question`) carries the "pick A, B, C, or D" cue.
    """

    body = shape_text(question.text, language=language)
    frame = _OPTION_FRAMES.get(language, _OPTION_FRAMES["en"])
    option_strs = [
        frame.format(key=opt.key, text=shape_text(opt.text, language=language))
        for opt in question.options
    ]
    if not body.endswith((".", "?", "!")):
        body = body + "."
    return " ".join([body, *option_strs])


def shape_topic_list(
    topics: Sequence[Mapping[str, object]],
    *,
    language: str,
) -> str:
    """Render `[{topic_id, label, count, has_fallback}]` as a TTS prompt.

    Output: "Available topics: Azure Networking, Azure Storage, Azure
    Identity (currently in another language)." — comma-separated; trailing
    has_fallback annotation reads aloud cleanly.
    """

    if not topics:
        # Caller decides the empty-list copy; we just return a placeholder
        # the agent's phrasing block can detect and replace.
        return ""

    parts: list[str] = []
    for topic in topics:
        label = str(topic.get("label", "")).strip()
        has_fallback = bool(topic.get("has_fallback", False))
        if not label:
            continue
        if has_fallback:
            label = f"{label} (fallback)"
        parts.append(shape_text(label, language=language))
    return ", ".join(parts)


def shape_results_summary(
    *,
    score: float,
    max_score: float,
    percentage: float,
    is_pass: bool,
    language: str,
) -> str:
    """Render the numeric portion of a results summary in voice-safe form.

    The pass/fail wording itself comes from the active-language phrasing
    block (`pass_message` / `fail_message`); this helper produces only the
    "X out of Y" line so the localisation responsibility stays in the
    phrasing layer.
    """

    score_int = int(score) if score.is_integer() else round(score, 1)
    max_int = int(max_score) if max_score.is_integer() else round(max_score, 1)
    return shape_text(
        f"{score_int} / {max_int} ({percentage:.0f} percent).",
        language=language,
    )


def shape_verdict(verdict: str, *, language: str) -> str:
    """Render a verdict label in TTS-safe form for the active language.

    The contract layer's `feedback_correct` / `feedback_incorrect` blocks
    are the load-bearing copy; this helper is the structured fallback used
    when a downstream surface needs a verdict word in isolation (audit
    rendering, support diagnostics).
    """

    labels = {
        "en": {
            "correct": "Correct.",
            "incorrect": "Not quite.",
            "partial": "Partially correct.",
            "unanswered": "No answer captured.",
        },
        "fr": {
            "correct": "Correct.",
            "incorrect": "Pas tout à fait.",
            "partial": "Partiellement correct.",
            "unanswered": "Aucune réponse.",
        },
        "es": {
            "correct": "Correcto.",
            "incorrect": "No es la respuesta.",
            "partial": "Parcialmente correcto.",
            "unanswered": "Sin respuesta.",
        },
    }
    lang_labels = labels.get(language, labels["en"])
    label = lang_labels.get(verdict, verdict)
    return shape_text(label, language=language)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _replace_urls(text: str, language: str) -> str:
    """Replace raw URLs with a phonetic-safe spoken form."""

    if "http" not in text.lower():
        return text
    verb = _URL_PHONETIC_VERB.get(language, _URL_PHONETIC_VERB["en"])

    def _sub(match: re.Match[str]) -> str:
        url = match.group(0)
        host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
        # Trim trailing punctuation that often follows a URL in prose.
        host = host.rstrip("./?,;)\"'")
        spoken = host.replace(".", " dot ").replace("/", " slash ")
        return f"{verb}{spoken}"

    return _URL_RE.sub(_sub, text)


def _spell_small_numerals(text: str, language: str) -> str:
    """Spell integer tokens 0..20 in the active language."""

    table = _NUMERAL_WORDS.get(language, _NUMERAL_WORDS["en"])

    def _sub(match: re.Match[str]) -> str:
        token = match.group(1)
        word = table.get(token)
        if word is None:
            return token
        return word

    return _INT_TOKEN_RE.sub(_sub, text)


__all__ = [
    "shape_question",
    "shape_results_summary",
    "shape_text",
    "shape_topic_list",
    "shape_verdict",
]
