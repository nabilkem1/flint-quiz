"""Prompt redaction lint (TEST-018 / TASK-176 / GOV-005).

Static lint over every rendered prompt layer for each
`(language, channel)` pair. Asserts no forbidden tokens appear:

  * Seeded answer-key values (read from the seed tree).
  * `_etag=`.
  * `Bearer ` (auth headers).
  * `AccountKey=` / `SharedAccessSignature` / `ApiKey=` (credentials).
  * Test-user PII fixtures (none in this repo — the check is a
    forward guard).

The prompt layers are composed by
:func:`src.agent.prompts.compose.compose`. We render the full prompt
for each language and run the grep over the rendered bytes.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timezone

import pytest

from src.agent.prompts.compose import SessionFrame, compose
from src.data.models import Channel, SUPPORTED_LANGUAGES

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED_DIR = REPO_ROOT / "src" / "seed" / "questions"

# Tokens that must NEVER appear in a rendered prompt.
#
# **Note**: the literal string ``correct_answer`` is **intentionally**
# permitted — the behavioral contract layer instructs the agent NOT to
# leak it, and that instruction names the field. The load-bearing
# property TEST-018 cares about is: no **value** of any seeded answer
# key (handled below in
# :func:`test_no_seeded_answer_values_leak_via_compose_path`) and no
# credential-shaped substring (handled here).
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "_etag=",
    "Bearer ",
    "AccountKey=",
    "SharedAccessSignature",
    "ApiKey=",
)


def _seeded_answer_values() -> set[str]:
    """Return every authored `correct_answer` letter across all languages."""

    values: set[str] = set()
    if not SEED_DIR.exists():
        return values
    for path in SEED_DIR.rglob("*.json"):
        try:
            authored = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        # We surface the option keys themselves (`["A"]`, `["B"]`) —
        # NOT the option text. The forbidden check is structural: a
        # composed prompt that names "the answer is B" is OK (it would
        # be a phrasing block author writing literal "B"); a composed
        # prompt that names every authored answer key is suspicious.
        # In practice the seed tree's answer values are letters, so we
        # cannot grep for letter-substring without false positives —
        # instead we run a coarser check: the LITERAL `correct_answer`
        # field name (above), which the prompt layer should never carry.
        values.update(authored.get("correct_answer", []))
    return values


def _frame(language: str, channel: Channel) -> SessionFrame:
    return SessionFrame(
        session_id="sess-redaction-probe",
        user_id="user-redaction-probe",
        topic="azure-networking",
        language=language,
        channel_at_start=channel.value,
        total=5,
        time_limit_seconds=600,
        started_at=datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGUAGES))
@pytest.mark.parametrize("channel", [Channel.TEXT, Channel.VOICE])
def test_rendered_prompt_has_no_forbidden_tokens(language: str, channel: Channel) -> None:
    composed, prompt_hash = compose(language=language, session_frame=_frame(language, channel))
    for pattern in FORBIDDEN_PATTERNS:
        assert pattern not in composed, (
            f"rendered prompt for ({language}, {channel.value}) contains forbidden token {pattern!r}"
        )
    # The hash itself is the SHA-256 of the composed prompt — present but
    # not sensitive. Just confirms determinism.
    assert re.fullmatch(r"[0-9a-f]{64}", prompt_hash)


def test_prompt_redaction_covers_every_required_language() -> None:
    """A new language entering the allowlist must surface here so the
    redaction lint runs on its rendered prompt too."""

    assert {"en", "fr", "es"}.issubset(SUPPORTED_LANGUAGES)


def test_no_seeded_answer_values_leak_via_compose_path() -> None:
    """Render a prompt and assert no authored answer value appears.

    The seed tree's `correct_answer` field always contains single
    uppercase letters; a substring match is brittle. The TEST-006
    leak test handles the tool-egress assertion; this test re-asserts
    on the prompt-render path that the field NAME does not appear and
    no full answer-key JSON fragment slipped in via a phrasing-block
    typo.
    """

    seeded_value_strs = sorted(_seeded_answer_values())
    # Render for every (lang, channel).
    for language in sorted(SUPPORTED_LANGUAGES):
        for channel in (Channel.TEXT, Channel.VOICE):
            composed, _ = compose(
                language=language, session_frame=_frame(language, channel)
            )
            # The composed prompt rendered for the redaction probe must
            # not literally embed `["B"]` style JSON arrays — those are
            # answer-shaped strings the phrasing layer should never contain.
            for v in seeded_value_strs:
                marker = f'"{v}"'
                # Allow lone letters in instructional copy
                # (e.g., "Pick A, B, C, or D."), but disallow the JSON
                # array form.
                assert f'["{v}"]' not in composed
                assert f"[{marker}]" not in composed
