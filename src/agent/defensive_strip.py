"""Defensive recursive strip of answer-key fields (TASK-088 / ADR-005).

The tool layer's typed boundary (`QuestionView`, `_ToolModel` with
``extra="forbid"``) is the load-bearing SEC-001 guarantee. This module is
the **third line of defence**: even if an upstream record were mistakenly
populated with `correct_answer`, or a future contributor widened a
projection without updating the model, this strip removes the field from
the outbound payload and emits a warning so the source can be remediated.

The strip walks dicts, lists, and tuples recursively. It is deliberately
**not** a denylist of all possibly-sensitive fields — it targets only the
known answer-key field names. Broader leakage prevention lives in the
typed models above; this layer's job is to make sure the literal token
``correct_answer`` cannot appear in a tool response.

Logging behaviour (per the prompt): a strip that DID act fires a warning,
not an error, so callers can still complete the request — but the warning
surfaces in App Insights so the upstream bug is visible.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Synonyms checked at every dict key. The literal `correct_answer` is the
# load-bearing one (it is the spelling that lives in AI Search and the
# seed JSON); the camelCase and `answer_key` variants are belt-and-braces
# for fields that may sneak through a naming-convention mismatch.
_FORBIDDEN_KEYS: frozenset[str] = frozenset({"correct_answer", "correctAnswer", "answer_key"})


def strip_answer_key(payload: Any) -> tuple[Any, bool]:
    """Recursively remove forbidden keys from `payload`.

    Returns ``(cleaned, found)`` — ``found=True`` iff any forbidden key was
    present at any nesting level. A ``True`` here is a P1 leak event; the
    caller MUST emit ``agent.answer_key_strip`` to telemetry so the
    upstream bug surfaces.

    The strip is **non-destructive** on the input — it returns a fresh
    structure rather than mutating in place. Pydantic-style model
    instances are first converted via ``model_dump`` so the strip operates
    on a plain Python tree.
    """

    found_flag: list[bool] = [False]
    cleaned = _walk(payload, found_flag)
    if found_flag[0]:
        logger.warning(
            "tool.defensive_strip.answer_key_present",
            extra={"forbidden_keys": sorted(_FORBIDDEN_KEYS)},
        )
    return cleaned, found_flag[0]


def _walk(node: Any, found: list[bool]) -> Any:
    # Pydantic v2 models expose `model_dump`; convert before walking so the
    # strip can mutate a plain dict and the model's `extra="forbid"`
    # contract does not block construction downstream.
    if hasattr(node, "model_dump") and callable(node.model_dump):
        try:
            node = node.model_dump(mode="json")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — fall through to raw walk
            pass
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for k, v in node.items():
            if k in _FORBIDDEN_KEYS:
                found[0] = True
                continue
            cleaned[k] = _walk(v, found)
        return cleaned
    if isinstance(node, list):
        return [_walk(item, found) for item in node]
    if isinstance(node, tuple):
        return tuple(_walk(item, found) for item in node)
    return node


__all__ = ["strip_answer_key"]
