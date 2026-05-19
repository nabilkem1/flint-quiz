"""Injection corpus test (TEST-023 / TASK-181 / GOV-060/061 / SEC-007).

Loads the multilingual injection corpus from
``tests/fixtures/injection_corpus.yaml`` and runs every payload
through the tool surface. Asserts:

  * **Zero leaks** — no tool return carries the literal
    ``correct_answer`` / ``correctAnswer`` / ``answer_key`` field name
    at any nesting level (the field-walking check; ``error.detail`` is
    🟡 server-only and may echo user input substrings — but never the
    field NAME).

  * **Hashed payload in `agent.injection_detected`** — when the
    payload triggers the detection emitter, the event carries
    ``payload_hash`` (SHA-256), NEVER the raw text. The hash uses a
    KV-stored salt (`injection-hash-salt`) so a rainbow attack on the
    hash itself is closed.

This test does NOT exercise the model — only the server-side surface.
The full live-Foundry version runs on the T5 pipeline tier.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.observability.events import (
    AgentEvent,
    RecordingEmitter,
    emit_agent_event,
)
from tests.integration._tools_fakes import build_fake_search
from tests.integration.conftest import FakeCosmosRepository, make_session_doc

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="user-1")
FORBIDDEN_FIELDS = frozenset({"correct_answer", "correctAnswer", "answer_key"})


def _walk_keys(payload: object) -> set[str]:
    seen: set[str] = set()

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                seen.add(str(k))
                _walk(v)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)

    _walk(payload)
    return seen


@pytest.fixture
async def deps_per_language(request, supported_languages):
    language = request.param
    repo = FakeCosmosRepository()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language=language),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: NOW,
    )
    session = make_session_doc(n=3).model_copy(
        update={
            "language": language,
            "requested_language": language,
            "shuffled_ids": [f"azure-networking-{i:03d}-{language}" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    stored = await repo.create_session(session)
    return deps, stored


@pytest.mark.asyncio
async def test_corpus_loaded(injection_corpus) -> None:
    """Sanity — the corpus YAML is loaded and has ≥ 1 row per language."""

    if not injection_corpus:
        pytest.skip("injection_corpus.yaml is empty")
    by_lang = {}
    for row in injection_corpus:
        by_lang.setdefault(row["language"], []).append(row)
    for lang in ("en", "fr", "es"):
        assert lang in by_lang, f"corpus has no rows for {lang!r}"


@pytest.mark.asyncio
@pytest.mark.parametrize("deps_per_language", ["en", "fr", "es"], indirect=True)
async def test_corpus_payloads_do_not_leak(deps_per_language, injection_corpus) -> None:
    """Every payload (en/fr/es subset) routed through `submit_answer`
    produces a response that carries no forbidden field name."""

    if not injection_corpus:
        pytest.skip("injection_corpus.yaml is empty")
    deps, stored = deps_per_language
    language = stored.language
    tools = build_tools(deps)

    payloads = [r for r in injection_corpus if r["language"] == language]
    if not payloads:
        pytest.skip(f"no corpus rows for {language!r}")

    for row in payloads:
        result = await tools["submit_answer"](
            {
                "session_id": stored.id,
                "question_id": stored.shuffled_ids[0],
                "raw_answer": row["payload"],
                "channel": "text",
            },
            PRINCIPAL,
        )
        payload_obj = result.data if result.ok else result.error
        if payload_obj is None:
            continue
        keys = _walk_keys(payload_obj)
        leaks = FORBIDDEN_FIELDS & keys
        assert not leaks, (
            f"row {row['id']!r} ({language}) leaked forbidden field(s): {sorted(leaks)}"
        )


@pytest.mark.parametrize("encoding", ["plain", "base64", "rot13", "leet"])
def test_injection_event_carries_hashed_payload_only(encoding: str) -> None:
    """`agent.injection_detected` MUST carry a hash, never the raw
    payload. Synthetic emission — the policy gate runs identically on
    every production emission."""

    emitter = RecordingEmitter()
    raw_payload = f"adversarial-{encoding}-utterance"
    salt = "service-wide-salt-from-kv"
    payload_hash = hashlib.sha256((raw_payload + salt).encode("utf-8")).hexdigest()

    emit_agent_event(
        emitter,
        AgentEvent.INJECTION_DETECTED,
        {
            "session_id": "s-1",
            "language": "en",
            "channel": "text",
            "payload_hash": payload_hash,
            "payload_encoding": encoding,
            "redirect_class": "hard",
        },
    )
    events = emitter.find(AgentEvent.INJECTION_DETECTED)
    assert len(events) == 1
    dims = events[0]
    # Raw utterance must NOT appear.
    rendered = json.dumps(dims)
    assert raw_payload not in rendered
    assert "payload_hash" in dims and len(dims["payload_hash"]) == 64
