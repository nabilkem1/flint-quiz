"""TASK-071 / GOV-001..003 — `compose()` is deterministic and content-addressed.

Three properties:

  1. Determinism. Same inputs → same composed text and same SHA-256
     across repeated calls (and across a fresh process state — exercised
     via `_clear_cache`).
  2. No timestamps / random elements bleed in. The composed text is
     a strict function of the four layer files and the SessionFrame.
  3. The committed `MANIFEST.json` matches disk byte-for-byte.

The redaction lint surface (TEST-018) covers forbidden substrings —
those tests live alongside 009-testing once the redaction corpus is
authored. This unit test covers the bits that must hold even before
that corpus exists.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.prompts.compose import (
    REQUIRED_SLOTS,
    SessionFrame,
    _clear_cache,
    _compute_manifest,
    compose,
    load_manifest,
    verify_manifest,
)
from src.common.exceptions import FlintConfigurationError, InvalidLanguageError


def _frame(language: str = "en") -> SessionFrame:
    return SessionFrame(
        session_id="f2c61e3a-bf85-4c1b-8f6b-1a4d0b2e9a44",
        user_id="8d2c9f70-9b3a-4a3e-b3e2-aa1f2b3c4d5e",
        topic="azure-networking",
        language=language,
        channel_at_start="text",
        total=5,
        time_limit_seconds=600,
        started_at=datetime(2026, 5, 17, 12, 34, 56, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("language", ["en", "fr", "es"])
def test_compose_is_deterministic_across_calls(language: str) -> None:
    a_text, a_hash = compose(language=language, session_frame=_frame(language))
    b_text, b_hash = compose(language=language, session_frame=_frame(language))
    assert a_hash == b_hash
    assert a_text == b_text


@pytest.mark.parametrize("language", ["en", "fr", "es"])
def test_compose_is_deterministic_across_cache_clear(language: str) -> None:
    _, first_hash = compose(language=language, session_frame=_frame(language))
    _clear_cache()
    _, second_hash = compose(language=language, session_frame=_frame(language))
    assert first_hash == second_hash


def test_compose_diverges_when_a_relevant_field_changes() -> None:
    # session_id is part of the hashed frame; flipping it must change the hash.
    a = _frame()
    b = SessionFrame(
        session_id="00000000-0000-0000-0000-000000000000",
        user_id=a.user_id,
        topic=a.topic,
        language=a.language,
        channel_at_start=a.channel_at_start,
        total=a.total,
        time_limit_seconds=a.time_limit_seconds,
        started_at=a.started_at,
    )
    _, ha = compose(language="en", session_frame=a)
    _, hb = compose(language="en", session_frame=b)
    assert ha != hb


def test_compose_diverges_when_language_changes() -> None:
    _, h_en = compose(language="en", session_frame=_frame("en"))
    _, h_fr = compose(language="fr", session_frame=_frame("fr"))
    assert h_en != h_fr


def test_compose_rejects_naive_datetime() -> None:
    naive = SessionFrame(
        session_id="x",
        user_id="y",
        topic="t",
        language="en",
        channel_at_start="text",
        total=1,
        time_limit_seconds=60,
        started_at=datetime(2026, 5, 17, 12, 0, 0),  # naive — no tzinfo
    )
    with pytest.raises(FlintConfigurationError):
        compose(language="en", session_frame=naive)


def test_compose_rejects_language_mismatch() -> None:
    frame = _frame("en")
    with pytest.raises(FlintConfigurationError):
        compose(language="fr", session_frame=frame)


def test_compose_rejects_unsupported_language() -> None:
    bogus = SessionFrame(
        session_id="x",
        user_id="y",
        topic="t",
        language="xx",
        channel_at_start="text",
        total=1,
        time_limit_seconds=60,
        started_at=datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(InvalidLanguageError):
        compose(language="xx", session_frame=bogus)


@pytest.mark.parametrize("language", ["en", "fr", "es"])
def test_every_required_slot_renders_into_composed_text(language: str) -> None:
    rendered, _ = compose(language=language, session_frame=_frame(language))
    # The yaml dump produces lines like `  greeting: |-` or `  greeting: >-`.
    # Looking for the key at line start within the phrasing layer is enough.
    for slot in REQUIRED_SLOTS:
        assert f"\n  {slot}:" in rendered, f"slot {slot!r} missing from rendered {language} prompt"


def test_manifest_matches_disk() -> None:
    verify_manifest()


def test_manifest_records_every_layer_file() -> None:
    declared = load_manifest()
    actual = _compute_manifest()
    assert declared == actual
    for required in (
        "identity.txt",
        "contract.txt",
        "session-frame-template.txt",
        "lang/en.yaml",
        "lang/fr.yaml",
        "lang/es.yaml",
    ):
        assert required in declared, f"manifest missing entry {required!r}"
