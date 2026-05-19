"""Domain exception hierarchy — single source of truth per specs/008-api §6.3.

No module may declare a new exception base outside this file. Tool functions
translate these to the wire-level error envelope (008-api §4.2.1). Internal
codes and stack traces never reach the LLM (see SEC-001 / 008-api §6.4).
"""

from __future__ import annotations


class FlintError(Exception):
    """Abstract base for every domain exception."""


class FlintValidationError(FlintError):
    """User-correctable input was invalid. HTTP 400 equivalent."""


class InvalidLanguageError(FlintValidationError):
    """Language code is not in the SEC-010 allowlist."""


class FlintAuthorizationError(FlintError):
    """Caller lacks the required claim or role. HTTP 403 equivalent."""


class FlintNotFoundError(FlintError):
    """A referenced resource does not exist. HTTP 404 equivalent."""


class SessionStateError(FlintError):
    """Attempted state transition is forbidden by 008-api §4.3.

    Raised by every repository write that would violate the session state
    machine (e.g. submit_answer on an Expired session, Scored -> Active).
    """

    def __init__(self, message: str, *, from_status: str | None = None, to_status: str | None = None) -> None:
        super().__init__(message)
        self.from_status = from_status
        self.to_status = to_status


class FlintConflictError(FlintError):
    """Conditional write lost the race (Cosmos 412).

    Internal exception — resolved inside the repository via re-read and either
    an idempotent no-op or one bounded retry. Never surfaces to the caller.
    """


class FlintUpstreamError(FlintError):
    """A downstream Azure service failed (Cosmos 5xx, Search 5xx, Storage 5xx)."""


class FlintConfigurationError(FlintError):
    """Misconfiguration. Fail loud at startup, never at request time."""


class AnswerLeakageError(FlintError):
    """P0 — a SERVER-tier field was about to cross the LLM boundary.

    Always pages on-call; halts the active session. See 008-api §0.1 / SEC-001.
    """
