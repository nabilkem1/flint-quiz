"""Pure sweeper logic — no Functions host or CAJ scheduler coupling.

Imported by both:

* ``src.sweeper.function_app`` — Functions timer-trigger binding (legacy /
  optional path; needs ``Microsoft.Web/serverFarms`` quota).
* ``src.sweeper.__main__`` — Container Apps Job entry point (one-shot per
  cron firing; no VM quota required).

Behavior, observability counters, and the 412-race / scope-guard
semantics are identical across both hosts; this module is the single
source of truth.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import timezone
from typing import Any

from azure.cosmos import exceptions as cosmos_exceptions

from src.common.exceptions import FlintConflictError, SessionStateError
from src.data.cosmos_repository import CosmosRepository
from src.data.models import SessionDoc

logger = logging.getLogger(__name__)

# Defaults match 008-api §4.7 and tasks/003 TASK-191. The host (Functions
# or CAJ) reads overrides from App Configuration at boot via the agent UAMI;
# for v1 we read straight from env, falling back to the documented defaults.
DEFAULT_MAX_STRANDED_SECONDS: int = 300  # voice:maxStrandedSeconds
DEFAULT_PAUSE_THRESHOLD_SECONDS: int = 600  # sessions:pauseThresholdSeconds
FEED_QUERY_AGE_SECONDS: int = 60  # _ts predicate (008-api §4.7 / TASK-191)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("sweeper.env_invalid_int", extra={"key": key, "value": raw})
        return default


class SweeperConfig:
    """Resolved sweeper config; constructed once per host instance / job firing."""

    def __init__(self) -> None:
        self.cosmos_endpoint = os.environ["COSMOS_ENDPOINT"]
        self.database = os.environ.get("COSMOS_DATABASE", "flint-quiz")
        self.sessions_container = os.environ.get("COSMOS_SESSIONS_CONTAINER", "sessions")
        self.allowed_container = os.environ.get("SWEEPER_ALLOWED_CONTAINER", "sessions")
        self.max_stranded_seconds = _env_int(
            "SWEEPER_MAX_STRANDED_SECONDS", DEFAULT_MAX_STRANDED_SECONDS
        )
        self.pause_threshold_seconds = _env_int(
            "SWEEPER_PAUSE_THRESHOLD_SECONDS", DEFAULT_PAUSE_THRESHOLD_SECONDS
        )

        # Scope guard: refuse to boot if the configured container is
        # anything other than `sessions`. The sweeper has no business
        # touching users, topics, or audit.
        if self.allowed_container != "sessions":
            raise RuntimeError(
                f"sweeper refused to start: SWEEPER_ALLOWED_CONTAINER={self.allowed_container!r}, "
                "expected 'sessions'"
            )


# ---------------------------------------------------------------------------
# Feed query + transition logic
# ---------------------------------------------------------------------------


def _seconds_since(then_iso: str, now_epoch: int) -> int:
    """Compute seconds between an ISO-8601 UTC timestamp and ``now_epoch``.

    The sweeper uses Cosmos ``_ts``-derived ``now_epoch`` (server clock) rather
    than the host's wall clock. Caller injects ``now_epoch`` from the ``_ts``
    predicate of the feed read to honor 008-api §4.7's "server time is
    authoritative" rule.
    """

    try:
        from datetime import datetime

        dt = datetime.fromisoformat(then_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(now_epoch - dt.timestamp())
    except (ValueError, TypeError):
        return 0


def _classify(doc: dict[str, Any], cfg: SweeperConfig, now_epoch: int) -> str | None:
    """Pick the transition that should win for this row.

    Returns one of: ``"stranded"``, ``"expired"``, ``"paused"``, or
    ``None`` (no action — the row will be re-evaluated on a later tick).
    """

    status = doc.get("status")
    current_index = int(doc.get("currentIndex") or 0)
    started_at = doc.get("startedAt")
    question_started_at = doc.get("questionStartedAt")
    time_limit = int(doc.get("timeLimitSeconds") or 0)

    if status == "Active" and current_index == 0 and started_at:
        if _seconds_since(started_at, now_epoch) > cfg.max_stranded_seconds:
            return "stranded"

    if status in ("Active", "Paused") and started_at and time_limit > 0:
        if _seconds_since(started_at, now_epoch) > time_limit:
            return "expired"

    if status == "Active" and current_index > 0 and question_started_at:
        if _seconds_since(question_started_at, now_epoch) > cfg.pause_threshold_seconds:
            return "paused"

    return None


async def _apply_transition(
    repo: CosmosRepository,
    session: SessionDoc,
    transition: str,
) -> tuple[bool, str | None]:
    """Run the chosen transition; treat 412 as a logged-and-skipped no-op."""

    try:
        if transition == "stranded":
            await repo.expire_session(session)
            return True, "stranded_released"
        if transition == "expired":
            await repo.expire_session(session)
            return True, "expired_swept"
        if transition == "paused":
            await repo.pause_session(session)
            return True, "paused_swept"
        return False, None
    except FlintConflictError:
        logger.info(
            "sweeper.skip_etag_race",
            extra={"session_id": session.id, "transition": transition},
        )
        return False, None
    except SessionStateError as exc:
        logger.info(
            "sweeper.skip_illegal_transition",
            extra={
                "session_id": session.id,
                "transition": transition,
                "from_status": exc.from_status,
                "to_status": exc.to_status,
            },
        )
        return False, None


async def run_sweeper_tick(cfg: SweeperConfig, repo: CosmosRepository) -> dict[str, int]:
    """One sweeper tick. Returns the per-metric counters for App Insights."""

    now_epoch = int(time.time())
    cutoff_ts = now_epoch - FEED_QUERY_AGE_SECONDS

    counters = {"stranded_released": 0, "expired_swept": 0, "paused_swept": 0, "scanned": 0}

    try:
        rows = await repo.sweeper_feed(max_ts=cutoff_ts)
    except cosmos_exceptions.CosmosHttpResponseError as exc:
        logger.error("sweeper.feed_query_failed", extra={"error": exc.message})
        return counters

    for doc in rows:
        counters["scanned"] += 1
        transition = _classify(doc, cfg, now_epoch)
        if transition is None:
            continue
        try:
            session = SessionDoc.model_validate(doc)
        except Exception as exc:  # noqa: BLE001 - malformed rows must not stop the tick
            logger.warning(
                "sweeper.skip_invalid_row",
                extra={"session_id": doc.get("id"), "error": str(exc)},
            )
            continue
        persisted, counter_key = await _apply_transition(repo, session, transition)
        if persisted and counter_key:
            counters[counter_key] += 1

    logger.info("sweeper.tick", extra=counters)
    return counters


__all__ = [
    "DEFAULT_MAX_STRANDED_SECONDS",
    "DEFAULT_PAUSE_THRESHOLD_SECONDS",
    "FEED_QUERY_AGE_SECONDS",
    "SweeperConfig",
    "run_sweeper_tick",
]
