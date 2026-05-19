"""Deterministic prompt composition + SHA-256 hashing (TASK-071 / GOV-001..003).

This module is the **only** load-bearing piece of the per-session prompt
contract. Three properties matter:

1. **Determinism.** `compose(language, session_frame)` is a pure function of
   its inputs and the four pinned layer files. Same inputs → same composed
   text → same hash. No timestamps, no random nonces, no model output, no
   network reads. Property-tested in
   `tests/unit/test_compose_determinism.py`.

2. **Content-addressing.** Every layer file (`identity.txt`, `contract.txt`,
   each `lang/*.yaml`, and `session-frame-template.txt`) carries its own
   SHA-256 in `MANIFEST.json`. `verify_manifest()` recomputes those hashes
   from the on-disk bytes and raises if any drifts. The composed-prompt
   hash is derived only **after** that check passes, so a single tampered
   byte in any layer aborts before a session can be started.

3. **Hash stability across a session.** The session frame is rendered from
   per-session **invariant** fields (`session_id`, `topic`, `language`,
   `total`, `time_limit_seconds`, `started_at`, `channel_at_start`,
   `user_id`). Per-turn state (current index, live channel) is delivered
   through tool results, not through the system prompt, so the
   start-of-session hash stays equal across every subsequent dispatch.
   A mid-session mismatch is therefore a P0 — it can only mean either
   layer tamper or a session outliving the deploy drain margin
   (009-gov §1.2).

The template-substitution syntax is `${name}` (`string.Template`), chosen
over `str.format` because (a) it tolerates `{`/`}` characters elsewhere in
the layer text and (b) `safe_substitute` would silently swallow missing
keys — we use strict `substitute` so an unrenderable frame fails loud at
session start.
"""

from __future__ import annotations

import hashlib
import json
import string
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src.common.exceptions import (
    FlintConfigurationError,
    InvalidLanguageError,
)
from src.data.models import SUPPORTED_LANGUAGES

_PROMPTS_DIR: Path = Path(__file__).resolve().parent
_IDENTITY_PATH: Path = _PROMPTS_DIR / "identity.txt"
_CONTRACT_PATH: Path = _PROMPTS_DIR / "contract.txt"
_FRAME_TEMPLATE_PATH: Path = _PROMPTS_DIR / "session-frame-template.txt"
_LANG_DIR: Path = _PROMPTS_DIR / "lang"
_MANIFEST_PATH: Path = _PROMPTS_DIR / "MANIFEST.json"

# Slots a phrasing block must populate (TASK-062). Mirror the enumeration in
# 009-gov §1.3 and 004-agent §7.3. The compose() call refuses to render if any
# slot is missing — a contributor adding a new language file gets a loud
# failure rather than a silently-degraded prompt.
REQUIRED_SLOTS: frozenset[str] = frozenset(
    {
        "greeting",
        "ask_topic",
        "frame_question",
        "feedback_correct",
        "feedback_incorrect",
        "topic_unavailable_fallback",
        "coverage_gap_consent",
        "score_preview_decline",
        "refusal_off_topic",
        "refusal_answer_key",
        "stay_on_task",
        "results_summary",
        "pass_message",
        "fail_message",
        "idle_reprompt",
    }
)

# Concatenation order (009-gov §1.1) — never reordered, never partially used.
_LAYER_ORDER: tuple[str, ...] = ("identity", "contract", "phrasing", "frame")


@dataclass(frozen=True, slots=True)
class SessionFrame:
    """Per-session invariant values rendered into the session-frame layer.

    Every field here MUST be stable for the entire lifetime of a session —
    that stability is what makes the persisted `prompt_hash` re-verifiable
    on every turn. Per-turn state (current question index, live channel
    after a switch, last-answered timestamp) is conveyed via tool results
    and the user-role message, never via this frame.

    `channel_at_start` is intentionally the *initial* channel; mid-session
    text↔voice switches do not invalidate the hash because the frame
    records start-state, not live state (FR-009 / 004-agent §8).
    """

    session_id: str
    user_id: str
    topic: str
    language: str
    channel_at_start: str
    total: int
    time_limit_seconds: int
    started_at: datetime

    def as_substitution(self) -> dict[str, str]:
        # Use the integer epoch second representation for `started_at` — the
        # ISO-formatted string is also stable but a fixed timezone (UTC) and
        # an explicit suffix make the hashed output independent of the host
        # locale. SessionDoc invariably stores UTC (008-api §0.4 / NFR-004).
        if self.started_at.tzinfo is None:
            raise FlintConfigurationError(
                "SessionFrame.started_at must be timezone-aware (UTC) — naive "
                "datetimes would let host TZ poison the prompt hash."
            )
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "topic": self.topic,
            "language": self.language,
            "channel_at_start": self.channel_at_start,
            "total": str(self.total),
            "time_limit_seconds": str(self.time_limit_seconds),
            "started_at": self.started_at.astimezone(self.started_at.tzinfo).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class _Layers:
    identity: str
    contract: str
    phrasing: str
    frame_template: str


# Module-level cache — the layer files are immutable at runtime per GOV-001.
# Reading them once per process avoids a filesystem hit on every dispatch
# (NFR-001 hot path). Tests that need to mutate behaviour should monkeypatch
# `_load_layers` rather than mutating the cache directly.
_LAYER_CACHE: dict[str, _Layers] = {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FlintConfigurationError(f"prompt layer missing: {path}") from exc


def _load_phrasing(language: str) -> str:
    if language not in SUPPORTED_LANGUAGES:
        raise InvalidLanguageError(
            f"language {language!r} not in allowlist {sorted(SUPPORTED_LANGUAGES)}"
        )
    raw = _read_text(_LANG_DIR / f"{language}.yaml")
    parsed: dict[str, Any] = yaml.safe_load(raw) or {}
    slots: dict[str, Any] = parsed.get("slots") or {}
    missing = REQUIRED_SLOTS - slots.keys()
    if missing:
        raise FlintConfigurationError(
            f"phrasing block {language!r} missing slots: {sorted(missing)}"
        )
    extras = slots.keys() - REQUIRED_SLOTS
    if extras:
        # An unexpected slot suggests a copy-paste with a typo, or a key
        # destined for a different feature surface. Either way it should
        # not silently propagate into the system prompt.
        raise FlintConfigurationError(
            f"phrasing block {language!r} has unexpected slots: {sorted(extras)}"
        )
    # Render the YAML block as a canonical text section. `sort_keys=True`
    # keeps the on-wire ordering deterministic regardless of how the
    # author laid out the source file (and `allow_unicode=True` keeps
    # accented characters as themselves, not `\uXXXX` escapes).
    rendered = yaml.safe_dump(
        {"language": parsed.get("language", language), "slots": dict(slots)},
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
    )
    return rendered


def _load_layers(language: str) -> _Layers:
    cached = _LAYER_CACHE.get(language)
    if cached is not None:
        return cached
    layers = _Layers(
        identity=_read_text(_IDENTITY_PATH),
        contract=_read_text(_CONTRACT_PATH),
        phrasing=_load_phrasing(language),
        frame_template=_read_text(_FRAME_TEMPLATE_PATH),
    )
    _LAYER_CACHE[language] = layers
    return layers


def _clear_cache() -> None:
    """Drop the module-level layer cache.

    Test-only hook (tests that monkeypatch the layer files need a clean
    slate). Not exported; tests reach in by full attribute path.
    """

    _LAYER_CACHE.clear()


def compose(language: str, session_frame: SessionFrame) -> tuple[str, str]:
    """Compose the per-session system prompt and return `(prompt, sha256_hex)`.

    Pure. Deterministic. No I/O beyond reading the cached layer files.
    """

    if session_frame.language != language:
        # A divergence here would either be a caller bug or a deliberate
        # attempt to render a language other than the session's pinned one.
        # Fail closed rather than render a hash that does not match the
        # session's promptHash.
        raise FlintConfigurationError(
            f"session_frame.language={session_frame.language!r} does not match "
            f"compose language={language!r}"
        )
    layers = _load_layers(language)
    try:
        rendered_frame = string.Template(layers.frame_template).substitute(
            session_frame.as_substitution()
        )
    except KeyError as exc:
        raise FlintConfigurationError(
            f"session-frame-template references missing key {exc.args[0]!r}"
        ) from exc

    sections: dict[str, str] = {
        "identity": layers.identity,
        "contract": layers.contract,
        "phrasing": layers.phrasing,
        "frame": rendered_frame,
    }
    # Fixed marker structure so an accidental concatenation order swap is
    # detectable by hash and by eyeball. The marker text itself is part of
    # the hashed bytes — a contributor reordering the layers would produce
    # a fresh hash mismatch on every session.
    composed = "\n\n".join(
        f"=== LAYER {idx + 1}: {name.upper()} ===\n{sections[name].rstrip()}"
        for idx, name in enumerate(_LAYER_ORDER)
    )
    digest = hashlib.sha256(composed.encode("utf-8")).hexdigest()
    return composed, digest


# ---------------------------------------------------------------------------
# Manifest (build-time content-addressing — TASK-071 step 1)
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compute_manifest() -> dict[str, str]:
    """Compute the layer-file SHA-256 map without reading any cached state.

    Used by `verify_manifest()` and by the build-time generator (the
    `python -m src.agent.prompts.compose` entrypoint at the bottom of
    this file). Keep this implementation byte-for-byte stable: the
    manifest committed to the repo must match exactly.
    """

    entries: dict[str, str] = {
        "identity.txt": _hash_file(_IDENTITY_PATH),
        "contract.txt": _hash_file(_CONTRACT_PATH),
        "session-frame-template.txt": _hash_file(_FRAME_TEMPLATE_PATH),
    }
    for lang_file in sorted(_LANG_DIR.glob("*.yaml")):
        entries[f"lang/{lang_file.name}"] = _hash_file(lang_file)
    return entries


def load_manifest() -> dict[str, str]:
    if not _MANIFEST_PATH.exists():
        raise FlintConfigurationError(
            f"prompts MANIFEST.json missing at {_MANIFEST_PATH}; regenerate via "
            f"`python -m src.agent.prompts.compose --write-manifest`"
        )
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def verify_manifest() -> None:
    """Recompute layer hashes and assert they match the committed manifest.

    Called at agent startup. Raises `FlintConfigurationError` if any
    layer drifts — the runtime must refuse to start a session on a
    tampered prompt tree.
    """

    declared = load_manifest()
    actual = _compute_manifest()
    if declared != actual:
        drift = {k: (declared.get(k), actual.get(k)) for k in declared.keys() | actual.keys() if declared.get(k) != actual.get(k)}
        raise FlintConfigurationError(
            f"prompt MANIFEST drift detected — refusing to compose. drift={drift!r}"
        )


def _write_manifest() -> dict[str, str]:
    """Regenerate MANIFEST.json from the on-disk layer files."""

    manifest = _compute_manifest()
    _MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "REQUIRED_SLOTS",
    "SessionFrame",
    "compose",
    "load_manifest",
    "verify_manifest",
]


if __name__ == "__main__":  # pragma: no cover - tooling entrypoint
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Prompt layer / manifest tools.")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Recompute and overwrite prompts/MANIFEST.json from disk.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify MANIFEST.json matches disk; non-zero exit on drift.",
    )
    args = parser.parse_args()
    if args.write_manifest:
        m = _write_manifest()
        print(json.dumps(m, indent=2, sort_keys=True))
    elif args.verify:
        try:
            verify_manifest()
        except FlintConfigurationError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        print("manifest OK")
    else:
        parser.print_help()
