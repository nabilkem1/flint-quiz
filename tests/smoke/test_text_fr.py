"""TEST-004 — text French smoke (TASK-165).

Same shape as the English smoke; French variants drive the normaliser:
"B", "lettre B", "réponse B", "la deuxième".
"""

from __future__ import annotations

import json

import pytest

from ._helpers import build_smoke_deps, run_end_to_end


def _answer(idx: int) -> str:
    return ["B", "lettre B", "réponse B", "la deuxième", "B"][idx % 5]


@pytest.mark.asyncio
async def test_text_fr_smoke_end_to_end() -> None:
    deps = await build_smoke_deps(language="fr")
    final = await run_end_to_end(
        deps,
        user_id="user-1",
        language="fr",
        topic="azure-networking",
        n=5,
        channel="text",
        answer_resolver=_answer,
    )
    assert final["status"] == "Scored"
    assert final["score"] == 5.0
    assert final["percentage"] == 100.0
    assert final["pass"] is True
    assert final["language"] == "fr"
    assert "correct_answer" not in json.dumps(final)
