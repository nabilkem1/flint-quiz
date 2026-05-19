"""Coverage-fallback helper tests (TASK-089 / FR-012 / GOV-025).

`suggest_fallback` is pure: same inputs → same output, no side effects.
The fallback order is asserted rung-by-rung; the `None` case (no
language has coverage) is the only path that aborts the consent flow
into "offer a different topic".
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.agent.coverage_fallback import suggest_fallback
from src.data.models import TopicDoc

NOW = datetime(2026, 5, 17, tzinfo=timezone.utc)


def _topic(counts: dict[str, int], *, default: str = "en") -> TopicDoc:
    return TopicDoc(
        id="azure-networking",
        topic_id="azure-networking",
        labels={lang: f"Topic {lang}" for lang in counts},
        counts=counts,
        default_language=default,
        enabled=True,
        updated_at=NOW,
    )


def test_rung_1_user_preferred_language_wins() -> None:
    topic = _topic({"en": 20, "fr": 0, "es": 15}, default="en")
    # User previously used Spanish — bias toward Spanish even though
    # the topic's defaultLanguage is English.
    assert suggest_fallback(
        topic, requested_lang="fr", n=5, user_preferred="es"
    ) == "es"


def test_rung_2_topic_default_language_when_no_user_preference() -> None:
    topic = _topic({"en": 20, "fr": 0, "es": 15}, default="en")
    assert suggest_fallback(topic, requested_lang="fr", n=5) == "en"


def test_rung_3_highest_coverage_when_default_missing() -> None:
    topic = _topic({"en": 3, "fr": 0, "es": 20}, default="en")
    # English exists but only has 3 questions; user asked for n=5 → es wins.
    assert suggest_fallback(topic, requested_lang="fr", n=5) == "es"


def test_returns_none_when_no_language_has_enough_coverage() -> None:
    topic = _topic({"en": 1, "fr": 0, "es": 2}, default="en")
    assert suggest_fallback(topic, requested_lang="fr", n=5) is None


def test_returns_none_when_only_requested_language_has_coverage() -> None:
    topic = _topic({"en": 0, "fr": 0, "es": 10}, default="es")
    assert suggest_fallback(topic, requested_lang="es", n=5) is None


def test_user_preferred_ignored_when_user_lang_lacks_coverage() -> None:
    topic = _topic({"en": 20, "fr": 0, "es": 2}, default="en")
    # User's preferred ES has only 2 entries; n=5 → fall through to en.
    assert suggest_fallback(
        topic, requested_lang="fr", n=5, user_preferred="es"
    ) == "en"


def test_user_preferred_does_not_pick_requested_lang() -> None:
    topic = _topic({"en": 20, "fr": 0, "es": 15}, default="en")
    # User said "fr" was a previous choice but fr has 0 coverage —
    # never return the requested language as its own fallback.
    assert (
        suggest_fallback(topic, requested_lang="fr", n=5, user_preferred="fr") == "en"
    )


def test_deterministic_tie_breaking_alphabetical() -> None:
    topic = _topic({"en": 10, "fr": 0, "es": 10, "de": 10}, default="en")
    # All three non-requested languages have count=10 → es < en alphabetically,
    # but rung 2 (defaultLanguage='en') wins before rung 3.
    assert suggest_fallback(topic, requested_lang="fr", n=5) == "en"

    topic_no_default = _topic({"en": 10, "fr": 0, "es": 10, "de": 10}, default="fr")
    # default lang == requested → skip rung 2; rung 3 sorts by (-count, lang)
    # so highest count tied → de (alphabetically first among de/en/es).
    assert (
        suggest_fallback(topic_no_default, requested_lang="fr", n=5) == "de"
    )
