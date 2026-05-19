"""Coverage-fallback helper (TASK-089 / FR-012 / GOV-025).

`suggest_fallback` is a **pure** function that proposes the best
fallback language for a `(topic, requested_language, n)` triple, given
the per-language counts on the topic row and any optional user
preference signal. It performs **no** side effects: no Cosmos writes,
no language switching. The caller (the agent, via the consent flow in
TASK-189) is responsible for explicitly invoking `set_language` if the
user agrees to the proposal.

Fallback order (per the task pack prompt):
  1. User's previously-used language (`detected_language` or last
     session's language), if it has `count >= n` for this topic.
  2. The topic's `default_language`, if it has `count >= n`.
  3. The highest-coverage language with `count >= n`.
  4. `None` — no language has coverage; the agent must offer a
     different topic. There is no language switch in this case; the
     session never proceeds in a language the user did not consent to.
"""

from __future__ import annotations

from typing import Mapping

from src.data.models import TopicDoc


def suggest_fallback(
    topic: TopicDoc,
    *,
    requested_lang: str,
    n: int,
    user_preferred: str | None = None,
) -> str | None:
    """Return the best fallback language for `topic`, or `None` if none.

    `topic.counts` is the authoritative per-language coverage. `requested_lang`
    is excluded from the candidate set (the caller already checked it has
    zero coverage). `user_preferred` lets the caller bias toward the user's
    previously-used language when it has enough coverage.

    Determinism: ties are broken by language code (ascending). Two consecutive
    calls with the same inputs always yield the same result.
    """

    counts: Mapping[str, int] = topic.counts or {}

    # Rung 1 — user's previously-used language.
    if (
        user_preferred
        and user_preferred != requested_lang
        and counts.get(user_preferred, 0) >= n
    ):
        return user_preferred

    # Rung 2 — topic's default language.
    default_lang = topic.default_language
    if (
        default_lang
        and default_lang != requested_lang
        and counts.get(default_lang, 0) >= n
    ):
        return default_lang

    # Rung 3 — highest-coverage candidate with `count >= n`.
    candidates = [
        (lang, count)
        for lang, count in counts.items()
        if lang != requested_lang and count >= n
    ]
    if candidates:
        # Sort by (-count, lang) so the highest count wins; ties → ascending lang.
        candidates.sort(key=lambda kv: (-kv[1], kv[0]))
        return candidates[0][0]

    return None


__all__ = ["suggest_fallback"]
