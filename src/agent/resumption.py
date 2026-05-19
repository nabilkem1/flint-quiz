"""Session resumption + channel-switch tolerance (TASK-067 / TASK-068).

Resumption is the single path the agent takes when a user reconnects on
an existing `session_id` — text-after-text, voice-after-voice, or a
mixed text↔voice handoff (FR-008, FR-009 / 004-agent §10, §8).

The contract is small but exact:

  1. **Cosmos is authoritative.** Every resumption reads the SessionDoc
     fresh; nothing is recovered from the Foundry thread alone.
  2. **Status gate.** Only `Active` and `Paused` sessions can be resumed.
     `Expired`, `Completed`, and `Scored` return a localised "this
     session is done — start a new one" result; the agent must NOT
     silently restart.
  3. **Language pin.** The session's persisted language wins. We do NOT
     re-detect on resume — a code-switched resume utterance ("oh wait,
     do this in French") is handled later by an explicit `set_language`
     call, not by this function.
  4. **Channel is metadata.** The `current_channel` arg records the
     connection that resumed; we surface it on `ResumeContext` so the
     caller can adapt TTS/voice-only behaviours, but durable state does
     not flip to a new channel (the SessionDoc keeps the start channel
     in `channel`; channel-switches mid-session do not invalidate the
     prompt hash because the session frame layer records `channel_at_start`,
     not live channel — see `src/agent/prompts/compose.py`).
  5. **Next-question pointer.** `next_question_id` is the entry at
     `session.shuffled_ids[session.current_index]`, or `None` if the
     session has answered every question.

This module is intentionally narrow. The agent factory wires it; tools
do not call into it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from src.agent.agent_thread import (
    SessionStore as ThreadAttachStore,
    ThreadClient,
    ThreadResolution,
    resolve_thread,
)
from src.common.exceptions import (
    FlintNotFoundError,
    SessionStateError,
)
from src.data.models import Channel, SessionDoc, SessionStatus

logger = logging.getLogger(__name__)


_RESUMABLE_STATUSES: frozenset[str] = frozenset(
    {SessionStatus.ACTIVE.value, SessionStatus.PAUSED.value}
)


class SessionStore(ThreadAttachStore, Protocol):
    """Cosmos repository subset needed for resumption.

    Combines the `attach_thread_id` write used by `agent_thread.resolve_thread`
    with the partition-scoped `get_session` read used here.
    """

    async def get_session(self, session_id: str, user_id: str) -> SessionDoc: ...


@dataclass(frozen=True, slots=True)
class ResumeContext:
    """Everything the agent factory needs to continue a session.

    Designed to be the **single** return shape callers branch on. If the
    session is non-resumable, `resumable=False` and `next_question_id`
    is None; the caller emits the localised "session is done" line.
    """

    session: SessionDoc
    thread: ThreadResolution
    current_channel: Channel
    language: str
    resumable: bool
    next_question_id: str | None
    answered_count: int
    total: int
    is_channel_switch: bool


async def resume_from_session(
    *,
    session_id: str,
    user_id: str,
    current_channel: Channel,
    session_store: SessionStore,
    thread_client: ThreadClient,
) -> ResumeContext:
    """Rehydrate a session from Cosmos and resolve its Foundry thread.

    Raises:
        FlintNotFoundError: no such `session_id` for this user. The
            caller is the channel layer; it should fall through to the
            "start fresh" path rather than retry.

    All other failure modes are encoded in the returned `ResumeContext`
    (e.g., `resumable=False` for terminal-status sessions).
    """

    session = await session_store.get_session(session_id, user_id)
    status_value = session.status if isinstance(session.status, str) else session.status.value
    resumable = status_value in _RESUMABLE_STATUSES

    thread = await resolve_thread(
        session=session,
        thread_client=thread_client,
        session_store=session_store,
    )

    answered_count = len(session.answers)
    next_question_id: str | None = None
    if resumable and session.current_index < len(session.shuffled_ids):
        next_question_id = session.shuffled_ids[session.current_index]

    session_channel = session.channel if isinstance(session.channel, str) else session.channel.value
    is_channel_switch = session_channel != current_channel.value

    if is_channel_switch:
        # Channel switches are observable but not state-changing. Logged
        # at INFO so the operability dashboard (008-observability) can
        # count them; the user-visible behaviour is the re-acknowledgement
        # line, emitted by the agent factory after this returns.
        logger.info(
            "session.channel_switch",
            extra={
                "session_id": session.id,
                "from_channel": session_channel,
                "to_channel": current_channel.value,
            },
        )

    return ResumeContext(
        session=session,
        thread=thread,
        current_channel=current_channel,
        language=session.language,
        resumable=resumable,
        next_question_id=next_question_id,
        answered_count=answered_count,
        total=len(session.shuffled_ids),
        is_channel_switch=is_channel_switch,
    )


__all__ = ["ResumeContext", "SessionStore", "resume_from_session"]
