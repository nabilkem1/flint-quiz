"""TTS streaming + defensive markdown strip (TASK-103 / TASK-108).

The voice channel re-uses the same `shape_text` helper from
``src/agent/tts_shaper.py`` (005-tools TASK-087) for the bulk of the
TTS-friendly rendering. This module is the **last line of defence**:
even if a tool slipped a markdown char past the typed boundary and the
shaper, the bytes that hit the TTS engine never carry it. When the
strip actually has to act, an `agent.tts_strip` warning fires so the
upstream emitter can be fixed.

Per the FORBIDDEN ACTIONS list, the strip targets only the published
markdown / URL surface. It does NOT touch semantic content — sentence
structure, punctuation, accents — that is the shaper's job.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Protocol

from src.agent.tts_shaper import shape_text

logger = logging.getLogger(__name__)

# Mirrors `src/agent/tts_shaper._FORBIDDEN_MARKDOWN_RE` but is duplicated
# inline so a TTS-bound string can be checked without paying the full
# shaper cost (the strip is a hot-path defence; the shaper is a render
# pass).
_DEFENSIVE_MARKDOWN_RE = re.compile(r"[*`#\[\]~_]+")
_DEFENSIVE_URL_RE = re.compile(r"https?://\S+", flags=re.IGNORECASE)


class _Emitter(Protocol):
    def emit(self, name: str, properties: Mapping[str, Any]) -> None: ...


class _NullEmitter:
    def emit(self, name: str, properties: Mapping[str, Any]) -> None:  # pragma: no cover
        return None


def defensive_strip(
    text: str,
    *,
    language: str,
    emitter: _Emitter | None = None,
    session_id: str | None = None,
) -> tuple[str, bool]:
    """Apply the last-mile markdown + URL strip before TTS.

    Returns ``(cleaned, fired)`` — ``fired=True`` iff the strip actually
    removed something. When `fired=True`, an `agent.tts_strip` warning is
    emitted to the supplied sink (App Insights via the production
    emitter) so the offending tool can be remediated upstream.
    """

    if not text:
        return "", False

    fired = bool(_DEFENSIVE_MARKDOWN_RE.search(text)) or bool(_DEFENSIVE_URL_RE.search(text))
    if not fired:
        return text, False

    # We DO run the shaper here so the URL replacement uses the
    # per-language phonetic verb ("link at …" / "lien à …" / "enlace en …").
    cleaned = shape_text(text, language=language)

    sink = emitter or _NullEmitter()
    sink.emit(
        "agent.tts_strip",
        {
            "language": language,
            "session_id": session_id,
            "stripped_chars": _count_stripped(text),
        },
    )
    logger.warning(
        "tts_pipeline.defensive_strip.fired",
        extra={
            "language": language,
            "session_id": session_id,
        },
    )
    return cleaned, True


def synthesise_chunks(
    text: str,
    *,
    language: str,
    emitter: _Emitter | None = None,
    session_id: str | None = None,
    max_chunk_chars: int = 240,
) -> list[str]:
    """Split a (defensively stripped) TTS string into stream-friendly chunks.

    The Realtime TTS surface accepts the whole string in one frame, but
    chunking at sentence boundaries lets the synthesiser start speaking
    sooner — first-byte latency drops, which the voice dashboard
    (TASK-109) measures. `max_chunk_chars` is a soft cap; we never split
    inside a word.
    """

    cleaned, _fired = defensive_strip(
        text, language=language, emitter=emitter, session_id=session_id
    )
    if not cleaned:
        return []

    # Split on sentence terminators. Keeping punctuation in the chunk
    # preserves the prosody cue for the TTS engine.
    sentences = _split_sentences(cleaned)
    chunks: list[str] = []
    buffer = ""
    for sentence in sentences:
        if not buffer:
            buffer = sentence
            continue
        if len(buffer) + 1 + len(sentence) > max_chunk_chars:
            chunks.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}"
    if buffer:
        chunks.append(buffer)
    return chunks


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_RE.split(text.strip()) if s]


def _count_stripped(text: str) -> int:
    """Best-effort count of forbidden chars present pre-strip.

    Used as a one-shot telemetry dimension — not load-bearing for the
    contract. The boolean ``fired`` flag is the load-bearing signal.
    """

    return sum(len(m.group(0)) for m in _DEFENSIVE_MARKDOWN_RE.finditer(text)) + sum(
        1 for _ in _DEFENSIVE_URL_RE.finditer(text)
    )


__all__ = ["defensive_strip", "synthesise_chunks"]
