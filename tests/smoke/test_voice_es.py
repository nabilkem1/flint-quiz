"""TEST-005 — Spanish voice smoke (TASK-166).

Voice-channel end-to-end with Spanish variants the normaliser must
handle: "B", "letra B", "opción B", "la segunda". The smoke runs the
agent loop through `submit_answer` with `channel="voice"`; STT/TTS
plumbing belongs to the production Realtime SDK adapter.

The live-Foundry flavour against the deployed Realtime endpoint runs
on T2/T5; this in-process flavour gates every merge.
"""

from __future__ import annotations

import json

import pytest

from ._helpers import build_smoke_deps, run_end_to_end


def _answer(idx: int) -> str:
    return ["B", "letra B", "opción B", "la segunda", "B"][idx % 5]


@pytest.mark.asyncio
async def test_voice_es_smoke_end_to_end() -> None:
    deps = await build_smoke_deps(language="es")
    final = await run_end_to_end(
        deps,
        user_id="user-1",
        language="es",
        topic="azure-networking",
        n=5,
        channel="voice",
        answer_resolver=_answer,
    )
    assert final["status"] == "Scored"
    assert final["score"] == 5.0
    assert final["percentage"] == 100.0
    assert final["pass"] is True
    assert final["language"] == "es"
    assert "correct_answer" not in json.dumps(final)
