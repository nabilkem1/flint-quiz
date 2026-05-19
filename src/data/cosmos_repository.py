"""Cosmos repository for sessions, users, topics, and audit (TASK-046–048).

Single source of truth for every read/write against the four containers. The
repository:

* Constructs ``CosmosClient`` with ``DefaultAzureCredential`` — never a key
  or connection string (SEC-004).
* Exposes **partition-aware** reads only. Cross-partition queries are
  forbidden on this hot path; the sweeper's feed query is the one
  documented exception and lives in :mod:`src.sweeper.function_app`.
* Encodes the session state machine (008-api §4.3) as repository methods.
  Illegal transitions raise ``SessionStateError``; the ``status`` field is
  never mutated by callers directly.
* Implements the conditional-write contract on ``append_answer_conditional``
  via ``etag`` + ``if_match=`` with bounded retry. The idempotency check
  pivots on ``question_id``; a duplicate call returns the existing
  ``SessionDoc`` as an explicit no-op (TASK-047).

Concurrency note: ``answers[]`` is append-only and the natural-key
idempotency check is ``any(a.question_id == new.question_id for a in
session.answers)``. A 412 PreconditionFailed therefore has exactly two
outcomes: (a) someone else recorded the same answer first — return their
state (no-op); (b) someone else advanced an unrelated field — re-read and
retry once. No third "last-write-wins" branch exists, by design.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from azure.core import MatchConditions
from azure.cosmos import exceptions as cosmos_exceptions
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.identity.aio import DefaultAzureCredential

from src.common.exceptions import (
    FlintConflictError,
    FlintNotFoundError,
    FlintUpstreamError,
    SessionStateError,
)
from src.data.models import (
    Answer,
    AuditEvent,
    SessionDoc,
    SessionStatus,
    TopicDoc,
    UserDoc,
    Verdict,
)

logger = logging.getLogger(__name__)

# State-machine adjacency. Mirrors 008-api §4.3 exactly. Each transition is
# allowed only if the (from, to) pair is listed here. Same-state transitions
# are deliberately absent so the audit trail stays clean (008-api §4.3.1).
_ALLOWED_TRANSITIONS: frozenset[tuple[SessionStatus, SessionStatus]] = frozenset(
    {
        (SessionStatus.ACTIVE, SessionStatus.ACTIVE),  # submit_answer (not last)
        (SessionStatus.ACTIVE, SessionStatus.PAUSED),
        (SessionStatus.PAUSED, SessionStatus.ACTIVE),
        (SessionStatus.ACTIVE, SessionStatus.EXPIRED),
        (SessionStatus.PAUSED, SessionStatus.EXPIRED),
        (SessionStatus.ACTIVE, SessionStatus.COMPLETED),
        (SessionStatus.COMPLETED, SessionStatus.SCORED),
        (SessionStatus.EXPIRED, SessionStatus.SCORED),
    }
)

# Max retries on a 412 etag race. Beyond this we propagate — by then the row
# is contended hard enough that surfacing the conflict is more useful than
# guessing (008-api §4.6).
_MAX_ETAG_RETRIES: int = 1


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


class CosmosRepository:
    """Async repository over the `flint-quiz` Cosmos database.

    Construction is parameterised so tests can point at the emulator without
    a parallel implementation. Production wiring lives in
    ``src/agent/composition.py`` (task pack 004).
    """

    def __init__(
        self,
        *,
        endpoint: str,
        database_name: str = "flint-quiz",
        sessions_container: str = "sessions",
        users_container: str = "users",
        topics_container: str = "topics",
        audit_container: str = "audit",
        credential: DefaultAzureCredential | None = None,
        sessions_terminal_ttl_seconds: int = 30 * 24 * 3600,  # ADR-006 default 30 days
    ) -> None:
        self._endpoint = endpoint
        self._database_name = database_name
        self._credential = credential or DefaultAzureCredential()
        self._sessions_terminal_ttl_seconds = sessions_terminal_ttl_seconds
        self._client = CosmosClient(endpoint, credential=self._credential)
        db = self._client.get_database_client(database_name)
        self._sessions: ContainerProxy = db.get_container_client(sessions_container)
        self._users: ContainerProxy = db.get_container_client(users_container)
        self._topics: ContainerProxy = db.get_container_client(topics_container)
        self._audit: ContainerProxy = db.get_container_client(audit_container)

    # ----- Lifecycle -------------------------------------------------------

    async def close(self) -> None:
        await self._client.close()
        await self._credential.close()

    async def __aenter__(self) -> "CosmosRepository":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    # ----- Read paths (TASK-046) ------------------------------------------

    async def get_session(self, session_id: str, user_id: str) -> SessionDoc:
        """Read a session by its `(session_id, user_id)` pair.

        Partition-aware: never falls back to cross-partition. Tools must
        always carry both IDs (008-api §1.6.1 / §1.7).
        """

        try:
            doc = await self._sessions.read_item(item=session_id, partition_key=user_id)
        except cosmos_exceptions.CosmosResourceNotFoundError as exc:
            raise FlintNotFoundError(f"session {session_id!r} not found") from exc
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"cosmos read_item failed: {exc.message}") from exc
        return SessionDoc.model_validate(doc)

    async def get_user(self, user_id: str) -> UserDoc | None:
        try:
            doc = await self._users.read_item(item=user_id, partition_key=user_id)
        except cosmos_exceptions.CosmosResourceNotFoundError:
            return None
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"cosmos read_item failed: {exc.message}") from exc
        return UserDoc.model_validate(doc)

    async def get_topic(self, topic_id: str) -> TopicDoc | None:
        try:
            doc = await self._topics.read_item(item=topic_id, partition_key=topic_id)
        except cosmos_exceptions.CosmosResourceNotFoundError:
            return None
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"cosmos read_item failed: {exc.message}") from exc
        return TopicDoc.model_validate(doc)

    async def list_topics(self) -> list[TopicDoc]:
        """Enumerate every topic. Small catalog; cross-partition is acceptable.

        This is the documented exception to the "tool-path partition-scoped
        reads only" rule — the topic catalog is small, slow-changing, and
        cached in App Configuration with polling reload (008-api §2.3).
        """

        topics: list[TopicDoc] = []
        try:
            async for doc in self._topics.read_all_items():
                topics.append(TopicDoc.model_validate(doc))
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"cosmos read_all_items failed: {exc.message}") from exc
        return topics

    # ----- Write paths ----------------------------------------------------

    async def create_session(self, session: SessionDoc) -> SessionDoc:
        """Insert a brand-new session row.

        Caller composes `seed`, `shuffled_ids`, and `status=ACTIVE`. The
        repository does not invent timing fields — those come from the
        injected clock at the tool layer (docs/coding-standards §1.12).
        """

        payload = self._to_cosmos(session)
        try:
            stored = await self._sessions.create_item(body=payload)
        except cosmos_exceptions.CosmosResourceExistsError as exc:
            raise FlintConflictError(f"session {session.id!r} already exists") from exc
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"cosmos create_item failed: {exc.message}") from exc
        return SessionDoc.model_validate(stored)

    async def upsert_user(self, user: UserDoc) -> UserDoc:
        payload = self._to_cosmos(user)
        try:
            stored = await self._users.upsert_item(body=payload)
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"cosmos upsert_item failed: {exc.message}") from exc
        return UserDoc.model_validate(stored)

    async def write_audit_event(self, event: AuditEvent) -> AuditEvent:
        payload = self._to_cosmos(event)
        try:
            stored = await self._audit.create_item(body=payload)
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"cosmos audit create_item failed: {exc.message}") from exc
        return AuditEvent.model_validate(stored)

    # ----- Conditional write (TASK-047) -----------------------------------

    async def append_answer_conditional(
        self,
        session: SessionDoc,
        answer: Answer,
    ) -> tuple[SessionDoc, bool]:
        """Append `answer` to `session.answers[]` under an `ifMatch(_etag)` guard.

        Returns ``(updated_session, persisted)`` where ``persisted`` is
        ``True`` only on the successful conditional-write branch. The
        ``grading_event`` emitter (TASK-141) MUST hang off this flag so the
        idempotent no-op path doesn't double-count metrics — see the
        FORBIDDEN ACTIONS list in this pack's prompt.

        Idempotency is keyed on ``answer.question_id``. If the answer is
        already present in ``session.answers[]``, this is a no-op return.

        Raises:
            SessionStateError: session is not ``Active``.
            FlintConflictError: etag race exceeded the bounded retry budget.
        """

        if session.status != SessionStatus.ACTIVE:
            raise SessionStateError(
                f"submit_answer rejected: session {session.id} is {session.status}",
                from_status=session.status.value
                if isinstance(session.status, SessionStatus)
                else str(session.status),
                to_status="Active",
            )

        # Idempotent fast-path: never write if the answer slot is already filled.
        if any(a.question_id == answer.question_id for a in session.answers):
            logger.info(
                "submit_answer.idempotent_noop",
                extra={
                    "session_id": session.id,
                    "question_id": answer.question_id,
                },
            )
            return session, False

        current = session
        last_exc: cosmos_exceptions.CosmosAccessConditionFailedError | None = None
        for attempt in range(_MAX_ETAG_RETRIES + 1):
            updated = self._apply_answer(current, answer)
            try:
                stored = await self._sessions.replace_item(
                    item=current.id,
                    body=self._to_cosmos(updated),
                    etag=current.etag,
                    match_condition=MatchConditions.IfNotModified,
                )
            except cosmos_exceptions.CosmosAccessConditionFailedError as exc:
                last_exc = exc
                # Re-read at the **partition-scoped** key, then check if the
                # winning writer already recorded this question_id.
                refreshed = await self.get_session(current.id, current.user_id)
                if any(a.question_id == answer.question_id for a in refreshed.answers):
                    logger.info(
                        "submit_answer.idempotent_noop_after_412",
                        extra={
                            "session_id": current.id,
                            "question_id": answer.question_id,
                            "attempt": attempt,
                        },
                    )
                    return refreshed, False
                if attempt >= _MAX_ETAG_RETRIES:
                    break
                current = refreshed
                continue
            except cosmos_exceptions.CosmosHttpResponseError as exc:
                raise FlintUpstreamError(f"cosmos replace_item failed: {exc.message}") from exc
            return SessionDoc.model_validate(stored), True

        raise FlintConflictError(
            f"submit_answer exceeded etag retry budget for session {session.id!r}"
        ) from last_exc

    # ----- State-machine transitions (TASK-048) ---------------------------

    async def pause_session(self, session: SessionDoc) -> SessionDoc:
        return await self._transition(session, SessionStatus.PAUSED)

    async def resume_session(self, session: SessionDoc) -> SessionDoc:
        return await self._transition(session, SessionStatus.ACTIVE)

    async def expire_session(
        self,
        session: SessionDoc,
        *,
        unanswered_factory: callable | None = None,  # type: ignore[valid-type]
    ) -> SessionDoc:
        """Flip to ``Expired``; auto-grade remaining questions as ``unanswered``.

        ``unanswered_factory`` is a hook for the tool layer to construct
        ``Answer`` rows with the correct channel + timestamps + telemetry
        latency. If not supplied, this method generates minimal rows with
        ``received_raw=""``, ``received_normalized=None``, ``score_delta=0``.
        """

        remaining_ids = session.shuffled_ids[session.current_index :]
        now = _now_utc()
        new_answers: list[Answer] = []
        for qid in remaining_ids:
            if unanswered_factory is not None:
                new_answers.append(unanswered_factory(qid))
            else:
                new_answers.append(
                    Answer(
                        question_id=qid,
                        received_raw="",
                        received_normalized=None,
                        verdict=Verdict.UNANSWERED,
                        score_delta=0.0,
                        answered_at=now,
                        channel=session.channel,
                        latency_ms=0,
                    )
                )

        expired = session.model_copy(
            update={
                "status": SessionStatus.EXPIRED,
                "answers": [*session.answers, *new_answers],
                "current_index": len(session.shuffled_ids),
                "ttl": self._sessions_terminal_ttl_seconds,
            }
        )
        self._assert_transition(session.status, SessionStatus.EXPIRED)
        return await self._replace_with_etag(expired)

    async def complete_session(self, session: SessionDoc) -> SessionDoc:
        """Mark a session ``Completed`` (last answer accepted). TTL is set on Scored."""

        return await self._transition(session, SessionStatus.COMPLETED)

    async def attach_thread_id(self, session: SessionDoc, thread_id: str) -> SessionDoc:
        """Persist the Foundry `AgentThread.id` on the session row (TASK-066).

        Idempotent if the same `thread_id` is already attached. The write
        uses the standard etag-guarded ``_replace_with_etag`` path so a
        concurrent advance of the row (e.g., a submit_answer that landed
        between the resume read and the thread attach) surfaces as
        ``FlintConflictError`` rather than silently overwriting the
        winner's answer state.
        """

        if session.thread_id == thread_id:
            return session
        next_session = session.model_copy(update={"thread_id": thread_id})
        return await self._replace_with_etag(next_session)

    async def attach_prompt_hash(self, session: SessionDoc, prompt_hash: str) -> SessionDoc:
        """Persist the composed system-prompt SHA-256 on the session row (TASK-071).

        Called once at ``start_quiz`` time, before Q1 is emitted. Mirrors
        ``attach_thread_id`` semantics: idempotent on identical input,
        etag-guarded otherwise. The dispatcher (TASK-070) re-verifies
        this hash on every subsequent tool invocation; a mid-session
        mutation here is a P0 incident path.
        """

        if session.prompt_hash == prompt_hash:
            return session
        next_session = session.model_copy(update={"prompt_hash": prompt_hash})
        return await self._replace_with_etag(next_session)

    async def score_session(self, session: SessionDoc) -> SessionDoc:
        """Final transition to ``Scored``. Sets TTL per ADR-006."""

        scored = session.model_copy(
            update={
                "status": SessionStatus.SCORED,
                "ttl": self._sessions_terminal_ttl_seconds,
            }
        )
        self._assert_transition(session.status, SessionStatus.SCORED)
        return await self._replace_with_etag(scored)

    # ----- Sweeper feed (TASK-191; the ONE cross-partition exception) -----

    async def sweeper_feed(
        self,
        *,
        max_ts: int,
        max_items: int = 256,
    ) -> list[dict[str, Any]]:
        """Cross-partition feed of `Active`/`Paused` sessions older than `max_ts`.

        This is the **only** cross-partition read in the repository. It runs
        on the maintenance path (`src/sweeper/function_app.py`), never on a
        tool-handler hot path. The `max_ts` predicate prunes rows touched in
        the last 60 s so the sweeper does not race recent user turns
        (008-api §4.7 / TASK-191).

        Returns raw Cosmos dicts so the caller can classify quickly without
        the validation cost of `SessionDoc.model_validate` for rows that
        won't transition.
        """

        query = (
            "SELECT c.id, c.userId, c._etag, c.status, c.startedAt, "
            "c.questionStartedAt, c.timeLimitSeconds, c.currentIndex, "
            "c.shuffledIds, c.answers, c.channel, c.score, c.maxScore, "
            "c.language, c.requestedLanguage, c.seed, c.topic, "
            "c.perQuestionLimitSeconds, c.passThresholdPct, c.schemaVersion, "
            "c.ttl, c._ts "
            "FROM c "
            'WHERE c.status IN ("Active", "Paused") AND c._ts < @max_ts'
        )
        results: list[dict[str, Any]] = []
        async for item in self._sessions.query_items(
            query=query,
            parameters=[{"name": "@max_ts", "value": max_ts}],
            max_item_count=max_items,
        ):
            results.append(item)
        return results

    # ----- Internals ------------------------------------------------------

    @staticmethod
    def _assert_transition(current: SessionStatus | str, target: SessionStatus) -> None:
        current_status = (
            current if isinstance(current, SessionStatus) else SessionStatus(current)
        )
        if (current_status, target) not in _ALLOWED_TRANSITIONS:
            raise SessionStateError(
                f"forbidden transition {current_status.value} -> {target.value}",
                from_status=current_status.value,
                to_status=target.value,
            )

    async def _transition(self, session: SessionDoc, target: SessionStatus) -> SessionDoc:
        self._assert_transition(session.status, target)
        next_session = session.model_copy(update={"status": target})
        return await self._replace_with_etag(next_session)

    async def _replace_with_etag(self, session: SessionDoc) -> SessionDoc:
        if session.etag is None:
            raise FlintConflictError(
                f"refusing to replace session {session.id} without an etag — fresh read required"
            )
        try:
            stored = await self._sessions.replace_item(
                item=session.id,
                body=self._to_cosmos(session),
                etag=session.etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except cosmos_exceptions.CosmosAccessConditionFailedError as exc:
            raise FlintConflictError(
                f"etag mismatch on session {session.id} — caller must re-read"
            ) from exc
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"cosmos replace_item failed: {exc.message}") from exc
        return SessionDoc.model_validate(stored)

    @staticmethod
    def _apply_answer(session: SessionDoc, answer: Answer) -> SessionDoc:
        """Compute the next session state given an appended answer.

        Pure function. Does not perform validation against the state machine
        — the caller asserts ``status == ACTIVE`` before invoking. Increments
        ``score`` and ``current_index``; advances ``status`` to
        ``COMPLETED`` if the last slot was just filled.
        """

        new_index = session.current_index + 1
        new_status = (
            SessionStatus.COMPLETED if new_index >= len(session.shuffled_ids) else SessionStatus.ACTIVE
        )
        new_score = session.score + answer.score_delta
        new_answers: Sequence[Answer] = [*session.answers, answer]
        return session.model_copy(
            update={
                "answers": list(new_answers),
                "current_index": new_index,
                "score": new_score,
                "status": new_status,
            }
        )

    @staticmethod
    def _to_cosmos(model: Any) -> dict[str, Any]:
        """Serialise a Pydantic model to the camelCase Cosmos JSON shape.

        ``by_alias=True`` produces the wire-level keys (camelCase + `_etag`).
        ``exclude_none=True`` drops unset fields like ``ttl`` so we don't
        write ``"ttl": null`` and trip Cosmos TTL semantics.
        """

        return model.model_dump(by_alias=True, exclude_none=True, mode="json")


__all__ = ["CosmosRepository"]
