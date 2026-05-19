"""Foundry `AgentThread` ↔ Cosmos `SessionDoc` linkage (TASK-066).

`AgentThread` is Foundry's per-conversation ephemeral state — it holds
the last few model turns so the LLM can phrase a coherent reply without
re-receiving the entire history every turn. **It is not authoritative.**
Durable session state (current index, answers, score, status) lives in
Cosmos per ADR-003; the thread is a UX latency aid we can drop on the
floor at any time.

This module is the **only** place the mapping
`session_id ↔ thread_id` is touched. Two helpers:

  * `resolve_thread(...)` — for a brand-new session, create a fresh
    Foundry thread and persist its ID. For a resumed session with an
    existing `thread_id`, look it up. If the lookup fails (Foundry
    expired the thread, deploy lost it), fall back to creating a fresh
    one and persisting the new ID. The agent re-states context from
    Cosmos on the first turn either way, so a fresh thread is a graceful
    degradation, not a failure.
  * `forget_thread(...)` — called by the sweeper / `score_session`
    pipeline to drop the Foundry thread once the session is terminal.
    No grading risk: Cosmos is authoritative.

The Foundry client is dependency-injected via a Protocol so the
integration tests in `tests/integration/test_resumption.py` and
`tests/integration/test_channel_switch.py` can run without a live
Foundry endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from src.data.models import SessionDoc

logger = logging.getLogger(__name__)


class ThreadClient(Protocol):
    """Subset of the Foundry projects-client thread surface we need.

    The production wiring binds this to
    `azure.ai.projects.aio.AIProjectClient.agents.threads`. Tests inject
    an in-memory fake. Both expose `create()` and `get()` with the same
    return shape.
    """

    async def create(self) -> "ThreadRef": ...

    async def get(self, thread_id: str) -> "ThreadRef | None": ...

    async def delete(self, thread_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ThreadRef:
    """Minimal projection of a Foundry AgentThread record.

    `id` is the only field we persist on the SessionDoc; everything else
    (the actual message history) stays Foundry-side and is read by the
    MAF runtime, not by our code.
    """

    id: str


class SessionStore(Protocol):
    """Subset of `CosmosRepository` we touch.

    We never mutate the SessionDoc directly from this module — the
    repository's `replace_session_thread_id` (or equivalent) is the only
    write path. Implementations supply it; for in-memory tests, a small
    fake suffices.
    """

    async def attach_thread_id(self, session: SessionDoc, thread_id: str) -> SessionDoc: ...


@dataclass(frozen=True, slots=True)
class ThreadResolution:
    """Outcome of `resolve_thread`.

    `created_fresh=True` indicates either a brand-new session or a
    Foundry-side miss that triggered a fresh thread. Either way, the
    agent must re-state context from Cosmos at the next turn — the
    caller uses this flag to decide whether to issue a re-acknowledgement
    line from the phrasing block (FR-008 / 004-agent §10).
    """

    thread: ThreadRef
    created_fresh: bool


async def resolve_thread(
    *,
    session: SessionDoc,
    thread_client: ThreadClient,
    session_store: SessionStore,
) -> ThreadResolution:
    """Return a usable AgentThread for `session`, persisting the ID on miss.

    Behaviour matrix:

      session.thread_id    Foundry lookup  →  outcome
      ───────────────────  ───────────────     ──────────────────────────
      None (fresh start)   create()            persist, created_fresh=True
      "<id>" (resume)      get() OK            persist=no-op, fresh=False
      "<id>" (resume)      get() returns None  create() + persist, fresh=True

    Foundry-side errors propagate. We do not retry here — the SDK does,
    and a hard failure should not be silently masked.
    """

    existing_id = session.thread_id
    if existing_id:
        existing = await thread_client.get(existing_id)
        if existing is not None:
            return ThreadResolution(thread=existing, created_fresh=False)
        # Foundry no longer has the thread (eviction, region migration,
        # deploy). Log once at INFO; the user-facing path is unaffected
        # because Cosmos still holds the durable state.
        logger.info(
            "agent_thread.resume.miss",
            extra={"session_id": session.id, "stale_thread_id": existing_id},
        )
    fresh = await thread_client.create()
    await session_store.attach_thread_id(session, fresh.id)
    return ThreadResolution(thread=fresh, created_fresh=True)


async def forget_thread(
    *,
    session: SessionDoc,
    thread_client: ThreadClient,
) -> None:
    """Drop the Foundry-side thread once a session is terminal.

    Called from `score_session` (003 TASK-048) and from the sweeper
    (003 TASK-191). Best-effort: a 404 here is fine — Foundry may have
    already evicted. Other errors are logged but never raised; the
    durable Cosmos row is the authority.
    """

    if not session.thread_id:
        return
    try:
        await thread_client.delete(session.thread_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "agent_thread.forget.failed",
            extra={"session_id": session.id, "thread_id_prefix": session.thread_id[:8]},
            exc_info=True,
        )


__all__ = [
    "SessionStore",
    "ThreadClient",
    "ThreadRef",
    "ThreadResolution",
    "forget_thread",
    "resolve_thread",
]
