"""Two-stage dead-air handling (TASK-105 / GOV-014).

Voice silence is dual-graded:

  * First idle (``voice:idleReprompSeconds``, default 30 s):
    the agent re-prompts the user once in the active language using
    the phrasing-block slot ``idle_reprompt``. Counts as a recovered
    silence; the timer resets on the next final transcript.

  * Second idle (``voice:idleCloseSeconds``, default 60 s **cumulative**
    since last user input): close the Realtime connection gracefully.
    Cosmos state is intact; the next ``submit_answer`` for the same
    ``session_id`` succeeds.

The handler is **clock-injected** so tests pin time deterministically.
It does **not** speak — it surfaces a verdict and the caller (the
Realtime runtime) renders the active-language phrasing block.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class IdleVerdict(str, Enum):
    OK = "ok"
    REPROMPT = "reprompt"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class IdleConfig:
    """Idle thresholds, sourced from AppConfig (`voice:idle*`).

    The defaults match GOV-014 / 009-gov §2.6. Values are in seconds; the
    "cumulative" window is measured against the **last user input**, so
    a re-prompt that lands but receives no reply continues counting toward
    the close threshold.
    """

    reprompt_seconds: int = 30
    close_seconds: int = 60


@dataclass(frozen=True, slots=True)
class IdleOutcome:
    """Verdict + the elapsed silence duration so callers can record
    `voice.idle.*` telemetry without re-computing."""

    verdict: IdleVerdict
    elapsed_seconds: int


@dataclass
class IdleHandler:
    """Stateful idle tracker for a single voice session.

    State is intentionally per-session — the Realtime runtime builds
    one handler per connection. Reset by `mark_input` when a final
    transcript arrives; queried by the silence watcher on a timer
    tick.
    """

    config: IdleConfig
    clock: Callable[[], datetime]
    _last_input_at: datetime
    _reprompt_fired: bool = False

    @classmethod
    def start(
        cls,
        *,
        config: IdleConfig | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> "IdleHandler":
        return cls(
            config=config or IdleConfig(),
            clock=clock,
            _last_input_at=clock(),
        )

    def mark_input(self) -> None:
        """Record that user input was received — resets the timers."""

        self._last_input_at = self.clock()
        self._reprompt_fired = False

    def tick(self, *, now: datetime | None = None) -> IdleOutcome:
        """Classify the current silence window against the thresholds.

        Order matters: a `close` verdict supersedes a `reprompt`. The
        re-prompt path fires **once** per silence window — the caller
        does not need to track that itself.
        """

        instant = now or self.clock()
        elapsed = max(
            0, int((instant - _aware(self._last_input_at)).total_seconds())
        )
        if elapsed >= self.config.close_seconds:
            return IdleOutcome(verdict=IdleVerdict.CLOSE, elapsed_seconds=elapsed)
        if elapsed >= self.config.reprompt_seconds and not self._reprompt_fired:
            self._reprompt_fired = True
            return IdleOutcome(verdict=IdleVerdict.REPROMPT, elapsed_seconds=elapsed)
        return IdleOutcome(verdict=IdleVerdict.OK, elapsed_seconds=elapsed)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


__all__ = ["IdleConfig", "IdleHandler", "IdleOutcome", "IdleVerdict"]
