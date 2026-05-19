"""Voice session length cap (TASK-105 / NFR-013).

Realtime billing is per-minute and runs to real money. A runaway
connection — user closed the tab without ending the call, model in a
loop, mic stuck open — must terminate gracefully:

  * Above the cap (`voice:maxSessionMinutes`, default 30) the cap
    handler asks the runtime to flush a farewell turn in the session
    language and close the WebRTC connection.
  * Durable state stays in Cosmos. The next ``submit_answer`` for the
    same ``session_id`` succeeds — the user can resume in text or in a
    fresh voice session.

The cap is **server-clock** — never trust the connection's own
heartbeat. Tests inject the clock so we don't have to actually wait 30
minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class SessionCapVerdict(str, Enum):
    OK = "ok"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class SessionCapConfig:
    """Cap config — sourced from AppConfig (`voice:maxSessionMinutes`)."""

    max_session_minutes: int = 30


@dataclass(frozen=True, slots=True)
class SessionCapOutcome:
    verdict: SessionCapVerdict
    elapsed_seconds: int


@dataclass
class SessionCap:
    """Stateful per-connection length cap.

    Built once at WebRTC connect time and ticked alongside the idle
    handler. The two are intentionally separate so each can be tested
    in isolation.
    """

    config: SessionCapConfig
    clock: Callable[[], datetime]
    _started_at: datetime

    @classmethod
    def start(
        cls,
        *,
        config: SessionCapConfig | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> "SessionCap":
        return cls(
            config=config or SessionCapConfig(),
            clock=clock,
            _started_at=clock(),
        )

    def tick(self, *, now: datetime | None = None) -> SessionCapOutcome:
        instant = now or self.clock()
        elapsed = max(
            0, int((instant - _aware(self._started_at)).total_seconds())
        )
        max_seconds = self.config.max_session_minutes * 60
        if elapsed >= max_seconds:
            return SessionCapOutcome(
                verdict=SessionCapVerdict.CLOSE, elapsed_seconds=elapsed
            )
        return SessionCapOutcome(
            verdict=SessionCapVerdict.OK, elapsed_seconds=elapsed
        )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


__all__ = [
    "SessionCap",
    "SessionCapConfig",
    "SessionCapOutcome",
    "SessionCapVerdict",
]
