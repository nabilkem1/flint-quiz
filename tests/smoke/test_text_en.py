"""TEST-003 — text English smoke (TASK-164).

In-process end-to-end:

  * start a 5-question quiz on `azure-networking` in English;
  * answer each question via English variants ("B", "option B",
    "letter B", "the second");
  * read the final result; assert `Scored`, 100% score, no
    `correct_answer` token anywhere in any payload.

The live-Playground flavour runs against a deployed Foundry agent in
T2/T5 pipeline tiers; this in-process smoke gates every merge.
"""

from __future__ import annotations

import json

import pytest

from ._helpers import build_smoke_deps, run_end_to_end


def _answer(idx: int) -> str:
    return ["B", "option B", "letter B", "the second", "B"][idx % 5]


@pytest.mark.asyncio
async def test_text_en_smoke_end_to_end() -> None:
    deps = await build_smoke_deps(language="en")
    final = await run_end_to_end(
        deps,
        user_id="user-1",
        language="en",
        topic="azure-networking",
        n=5,
        channel="text",
        answer_resolver=_answer,
    )
    assert final["status"] == "Scored"
    assert final["score"] == 5.0
    assert final["percentage"] == 100.0
    assert final["pass"] is True
    assert final["language"] == "en"
    # SEC-001 — no answer key anywhere.
    assert "correct_answer" not in json.dumps(final)
