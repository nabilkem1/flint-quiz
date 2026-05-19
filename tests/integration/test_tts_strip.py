"""Defensive TTS strip tests (TASK-108).

The strip is the last line of defence on the voice channel:

  * Removes markdown chars (`*`, `**`, `` ` ``, `#`, `[`, `]`, `~`, `_`).
  * Replaces raw http(s) URLs with the phonetic-safe spoken form.
  * Emits an `agent.tts_strip` warning to App Insights so the source
    tool can be remediated.

The shaper (`src/agent/tts_shaper.py`) does the same work earlier in
the pipeline; this strip is the belt-and-braces.
"""

from __future__ import annotations

import pytest

from src.voice.tts_pipeline import defensive_strip, synthesise_chunks


class _Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, name: str, properties: dict[str, object]) -> None:
        self.events.append((name, dict(properties)))


def test_defensive_strip_removes_markdown() -> None:
    sink = _Sink()
    cleaned, fired = defensive_strip(
        "Hello **world** — `option B`",
        language="en",
        emitter=sink,
        session_id="s-1",
    )
    assert fired is True
    for ch in "*`":
        assert ch not in cleaned
    # Warning fires on the strip — surfaces in App Insights.
    assert any(name == "agent.tts_strip" for name, _ in sink.events)


def test_defensive_strip_replaces_urls_phonetically() -> None:
    sink = _Sink()
    cleaned, fired = defensive_strip(
        "See https://learn.microsoft.com/azure for more details.",
        language="en",
        emitter=sink,
        session_id="s-1",
    )
    assert fired is True
    assert "http" not in cleaned
    assert "://" not in cleaned
    assert "learn" in cleaned and "dot" in cleaned


def test_defensive_strip_is_noop_when_clean() -> None:
    sink = _Sink()
    cleaned, fired = defensive_strip(
        "This is a clean voice-friendly sentence.",
        language="en",
        emitter=sink,
        session_id="s-1",
    )
    assert fired is False
    # No warning when nothing was stripped — the strip is the last line of defence;
    # firing on clean input would drown the signal.
    assert sink.events == []


@pytest.mark.parametrize(
    "raw,language",
    [
        ("Voici **la** question.", "fr"),
        ("Aquí está la `respuesta`.", "es"),
        ("Click [here](http://example.com) — _markdown_!", "en"),
    ],
)
def test_defensive_strip_per_language(raw: str, language: str) -> None:
    cleaned, fired = defensive_strip(raw, language=language)
    assert fired is True
    for ch in "*`[]_#~":
        assert ch not in cleaned


def test_synthesise_chunks_splits_on_sentences() -> None:
    text = (
        "This is the first sentence. This is the second one — and a bit longer. "
        "Third here?"
    )
    chunks = synthesise_chunks(text, language="en", max_chunk_chars=60)
    assert len(chunks) >= 2
    # No chunk exceeds the soft cap by a wide margin.
    assert all(len(c) <= 80 for c in chunks)


def test_synthesise_chunks_strips_first() -> None:
    """A tainted input is stripped BEFORE chunking — no chunk carries markdown."""

    text = "Option A: **VPN gateway**. Option B: front door. Option C: firewall."
    chunks = synthesise_chunks(text, language="en")
    for chunk in chunks:
        assert "*" not in chunk
        assert "`" not in chunk


def test_synthesise_chunks_empty_input_returns_empty_list() -> None:
    assert synthesise_chunks("", language="en") == []
