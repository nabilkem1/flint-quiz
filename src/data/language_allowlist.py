"""ISO 639-1 language allowlist validator (TASK-123 / SEC-010).

Single source of truth for "is this language code allowed in this
deploy?". The allowlist lives in App Configuration under the key
``languages:supported`` (semicolon- or comma-separated, e.g.,
``en,fr,es``) so a new language can be enabled at runtime once content
+ phrasing blocks are authored.

Every tool that accepts a language code routes through this module:

  * ``set_language`` — explicit user preference (FR-010 / FR-014).
  * ``start_quiz``   — session creation.
  * ``list_topics``  — catalog filter.
  * Seed loader      — ``src/seed/seed_index.py`` rejects records in
    disallowed languages so the index never carries them.

There is NO parallel constant duplicating the allowlist (FORBIDDEN
ACTIONS). The build-time constant ``SUPPORTED_LANGUAGES`` in
``src/data/models.py`` is the **fallback** used when AppConfig is not
reachable (e.g., unit tests, local dev without auth); the live allowlist
overrides at runtime when present.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from src.common.exceptions import InvalidLanguageError
from src.data.models import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)


_ALLOWLIST_KEY: str = "languages:supported"
_DEFAULT_TTL_SECONDS: float = 60.0  # short — AppConfig changes pick up within 60 s.

# An ISO 639-1 code is exactly two lowercase ASCII letters. Anything
# else is structurally invalid before we even consult the allowlist.
_ISO_639_1_RE = re.compile(r"^[a-z]{2}$")


class AppConfigReader(Protocol):
    """Subset of the AppConfig client we touch.

    Tests pass a dict-backed fake. Production wires
    `azure.appconfiguration.aio.AzureAppConfigurationClient` behind a
    thin adapter.
    """

    def get(self, key: str) -> str | None: ...


@dataclass
class LanguageAllowlist:
    """Cached read of `languages:supported` from AppConfig.

    Caches the parsed allowlist for ``ttl_seconds`` (default 60 s). On
    expiry the next ``validate`` call re-reads. The cache is intentionally
    short — the SEC-010 contract requires that toggling a language in
    AppConfig is observable in the runtime within minutes, not requires
    a restart.
    """

    reader: AppConfigReader | None
    ttl_seconds: float = _DEFAULT_TTL_SECONDS
    _cached_set: frozenset[str] | None = None
    _cached_at: float = 0.0

    def fetch(self) -> frozenset[str]:
        """Return the current allowlist, consulting AppConfig if stale.

        If the AppConfig reader is missing or raises, we fall back to
        the build-time constant ``SUPPORTED_LANGUAGES`` — the validator
        must never *crash* a tool call because AppConfig is briefly
        unreachable. A warning is logged so the divergence surfaces in
        App Insights.
        """

        now = time.monotonic()
        if self._cached_set is not None and (now - self._cached_at) < self.ttl_seconds:
            return self._cached_set

        live: frozenset[str] | None = None
        if self.reader is not None:
            try:
                raw = self.reader.get(_ALLOWLIST_KEY)
            except Exception:  # noqa: BLE001 — fall through to fallback
                logger.warning(
                    "language_allowlist.appconfig_unreachable",
                    extra={"key": _ALLOWLIST_KEY},
                    exc_info=True,
                )
                raw = None
            if raw:
                live = _parse(raw)

        resolved = live if live is not None else SUPPORTED_LANGUAGES
        self._cached_set = resolved
        self._cached_at = now
        return resolved

    def validate(self, code: str) -> str:
        """Return the normalised code on success; raise on disallowed.

        Normalisation: NFKD strip + lowercase + trim. The input is rejected
        if it is not a two-letter ASCII code OR if it is not in the
        live allowlist.

        Raises:
            InvalidLanguageError: code is structurally invalid OR not in
                the allowlist.
        """

        normalised = _normalise(code)
        allow = self.fetch()
        if not _ISO_639_1_RE.fullmatch(normalised):
            raise InvalidLanguageError(
                f"language {code!r} is not a valid ISO 639-1 code"
            )
        if normalised not in allow:
            raise InvalidLanguageError(
                f"language {normalised!r} not in allowlist {sorted(allow)}"
            )
        return normalised


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_DEFAULT_INSTANCE: LanguageAllowlist = LanguageAllowlist(reader=None)


def configure(reader: AppConfigReader, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
    """Wire the live AppConfig reader into the module-level validator.

    Called once at agent startup. Tests reset via ``reset_default()``.
    """

    global _DEFAULT_INSTANCE
    _DEFAULT_INSTANCE = LanguageAllowlist(reader=reader, ttl_seconds=ttl_seconds)


def reset_default() -> None:
    """Test-only — drop the configured reader so the next call uses the
    build-time constant."""

    global _DEFAULT_INSTANCE
    _DEFAULT_INSTANCE = LanguageAllowlist(reader=None)


def validate_language(code: str) -> str:
    """Module-level entry point (the spec's contract).

    Returns the normalised code; raises :class:`InvalidLanguageError`
    when the code is structurally invalid or not in the allowlist.
    """

    return _DEFAULT_INSTANCE.validate(code)


def current_allowlist() -> frozenset[str]:
    """Snapshot of the live allowlist. Used by docs / tooling, not by
    the hot path (which calls :func:`validate_language`)."""

    return _DEFAULT_INSTANCE.fetch()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalise(code: str) -> str:
    """NFKD strip + lowercase + trim. Empty / non-string inputs raise."""

    if not isinstance(code, str):
        raise InvalidLanguageError(f"language code must be a string, got {type(code).__name__}")
    stripped = unicodedata.normalize("NFKD", code).strip().lower()
    # Drop combining marks defensively (a code like "ñ" would never be
    # valid ISO 639-1 but defence-in-depth is cheap here).
    return "".join(ch for ch in stripped if not unicodedata.combining(ch))


def _parse(raw: str) -> frozenset[str]:
    """Parse the `languages:supported` value into a frozen set.

    Accepts comma- or semicolon-separated codes with optional whitespace.
    Empty / whitespace-only entries are silently dropped. Codes that
    fail the ISO 639-1 regex are skipped with a warning rather than
    crashing the load — operators get a chance to fix the config.
    """

    out: set[str] = set()
    for entry in re.split(r"[,;]", raw):
        token = _normalise(entry)
        if not token:
            continue
        if not _ISO_639_1_RE.fullmatch(token):
            logger.warning(
                "language_allowlist.unexpected_entry",
                extra={"entry": entry, "normalised": token},
            )
            continue
        out.add(token)
    return frozenset(out)


__all__ = [
    "LanguageAllowlist",
    "configure",
    "current_allowlist",
    "reset_default",
    "validate_language",
]
