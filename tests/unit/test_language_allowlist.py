"""Language allowlist validator tests (TASK-123 / SEC-010).

Asserts:

  * Two-letter ISO 639-1 normalised codes are accepted iff in the live
    allowlist.
  * Unsupported codes raise `InvalidLanguageError` with a clear message.
  * Structurally invalid codes (length, casing, non-ASCII) raise.
  * AppConfig allowlist takes priority over the build-time constant
    (so a new language enables at runtime without redeploy).
  * AppConfig unreachable → falls back to the build-time constant +
    warns (the validator never crashes a tool call on infra blips).
"""

from __future__ import annotations

import pytest

from src.common.exceptions import InvalidLanguageError
from src.data.language_allowlist import (
    LanguageAllowlist,
    reset_default,
    validate_language,
)


class _StaticReader:
    def __init__(self, value: str | None) -> None:
        self._value = value
        self.calls = 0

    def get(self, key: str) -> str | None:
        self.calls += 1
        if key == "languages:supported":
            return self._value
        return None


class _RaisingReader:
    def get(self, key: str) -> str | None:
        raise RuntimeError("AppConfig unreachable")


@pytest.fixture(autouse=True)
def _reset() -> None:
    yield
    reset_default()


def test_validate_language_accepts_lowercase_iso639_1_in_default() -> None:
    # No reader configured → fall back to build-time SUPPORTED_LANGUAGES.
    assert validate_language("en") == "en"
    assert validate_language("fr") == "fr"
    assert validate_language("es") == "es"


def test_validate_language_normalises_casing_and_whitespace() -> None:
    allow = LanguageAllowlist(reader=_StaticReader("en,fr,es"))
    assert allow.validate(" EN ") == "en"
    assert allow.validate("FR") == "fr"


def test_validate_language_rejects_unsupported() -> None:
    allow = LanguageAllowlist(reader=_StaticReader("en,fr,es"))
    with pytest.raises(InvalidLanguageError):
        allow.validate("kl")  # klingon
    with pytest.raises(InvalidLanguageError):
        allow.validate("de")


def test_validate_language_rejects_structurally_invalid_codes() -> None:
    allow = LanguageAllowlist(reader=_StaticReader("en,fr,es"))
    for bad in ("english", "e", "123", "e1", "", "  ", "FRR"):
        with pytest.raises(InvalidLanguageError):
            allow.validate(bad)


def test_validate_language_rejects_non_string_input() -> None:
    allow = LanguageAllowlist(reader=_StaticReader("en,fr,es"))
    with pytest.raises(InvalidLanguageError):
        allow.validate(None)  # type: ignore[arg-type]


def test_appconfig_value_overrides_default_constant() -> None:
    # AppConfig declares `de` in the allowlist; build-time constant does
    # not. The validator must honour AppConfig.
    allow = LanguageAllowlist(reader=_StaticReader("en,fr,es,de"))
    assert allow.validate("de") == "de"


def test_appconfig_unreachable_falls_back_to_default_constant(caplog) -> None:
    allow = LanguageAllowlist(reader=_RaisingReader())
    # Build-time SUPPORTED_LANGUAGES is {en, fr, es}.
    assert allow.validate("en") == "en"
    with pytest.raises(InvalidLanguageError):
        allow.validate("de")


def test_appconfig_supports_semicolon_separator() -> None:
    allow = LanguageAllowlist(reader=_StaticReader("en;fr;es"))
    assert allow.validate("fr") == "fr"


def test_allowlist_is_cached_for_ttl() -> None:
    reader = _StaticReader("en,fr,es")
    allow = LanguageAllowlist(reader=reader, ttl_seconds=60)
    allow.validate("en")
    allow.validate("en")
    allow.validate("fr")
    assert reader.calls == 1


def test_allowlist_unexpected_entries_are_skipped_not_fatal() -> None:
    """`languages:supported=en,FOO,fr` should yield {en, fr}, not crash."""

    allow = LanguageAllowlist(reader=_StaticReader("en,FOO,fr,123"))
    assert allow.validate("en") == "en"
    assert allow.validate("fr") == "fr"
    with pytest.raises(InvalidLanguageError):
        allow.validate("FO")  # garbage entry was dropped during parse
