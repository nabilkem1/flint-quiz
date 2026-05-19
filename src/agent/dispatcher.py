"""Tool dispatcher — the only path from MAF's tool-call loop to tool bodies.

Three load-bearing responsibilities (TASK-070 / GOV-010, GOV-012):

1. **Allowlist.** `ALLOWED_TOOLS` is a frozen constant of exactly the five
   tool names defined in 008-api §1 and 009-gov §2.1. A request for any
   other name returns the `E_INTERNAL`-shaped error envelope, emits
   `agent.unknown_tool` (with the rejected name and **nothing else**), and
   never invokes a tool body. P1 per 009-gov §15.

2. **Per-`(session_id, question_id)` mutex on `submit_answer`.** Two
   concurrent calls in the same process MUST produce exactly one tool-body
   invocation; the second caller awaits the same future and receives the
   same `ToolResult`. The TTL on the cache (60 s) keeps the dict bounded
   under load. This is the **intra-process** half of the SEC-006 defence;
   the cross-process half is the Cosmos `ifMatch` etag in
   `cosmos_repository.append_answer_conditional` (003 TASK-047). Both
   layers must hold for the GOV-012 guarantee.

3. **Prompt-hash verification.** On every dispatch that carries a
   `session_id`, the dispatcher re-runs `compose()` against the session's
   recorded language + invariant frame and compares the resulting SHA-256
   to `session.prompt_hash`. Mismatch → emit `agent.prompt_hash_mismatch`
   (P0), flip the session to `Paused`, return a localized "session
   paused" error envelope.

Tool **bodies** are injected at construction time. The dispatcher does
not import them — that boundary is enforced by the `import-linter`
contract in `pyproject.toml`. Tests inject a `dict[str, ToolCallable]`;
the production wiring (in `quiz_agent.create_agent`) imports the real
bodies from `src.agent.tools` (005-tools).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from src.agent.prompts.compose import SessionFrame, compose
from src.common.exceptions import (
    AnswerLeakageError,
    FlintAuthorizationError,
    FlintError,
    FlintNotFoundError,
    FlintValidationError,
    SessionStateError,
)
from src.data.models import Channel, SessionDoc, SessionStatus

logger = logging.getLogger(__name__)

# The frozen, single source of truth for the agent's tool surface
# (GOV-010 / 008-api §1). Exactly five names. Anywhere else in the codebase
# that needs to assert this set MUST import this constant — duplicate
# literal sets are a maintenance hazard.
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {"list_topics", "set_language", "start_quiz", "submit_answer", "get_results"}
)

# Subset that operates on an existing session — prompt-hash verification
# kicks in only for these (list_topics and set_language are pre-session
# entry points and have no SessionDoc to anchor against).
_SESSION_BOUND_TOOLS: frozenset[str] = frozenset(
    {"start_quiz", "submit_answer", "get_results"}
)

# Subset that mutates the session and therefore needs the mutex. Only
# `submit_answer` truly contends in v1 (start_quiz creates the row;
# get_results is read-only with status-machine side effects).
_MUTEX_TOOLS: frozenset[str] = frozenset({"submit_answer"})

_MUTEX_TTL_SECONDS: float = 60.0


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated caller's identity (audit §5.8 / GOV-063).

    `entra_oid` is the Microsoft Entra Object ID of the human user behind
    the channel. The dispatcher rejects any `args.user_id` that does not
    match — this is the **tool-argument impersonation defence** required
    by 009-gov §5.8, distinct from the upstream channel auth in 007.
    """

    entra_oid: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Wire-shape envelope returned by every tool (008-api §0.3 / §4.2).

    The agent loop will surface `ok=False` results to the model verbatim;
    `error.code` is one of the documented `E_*` codes. The dispatcher
    constructs these directly for its own failure modes (unknown tool,
    auth mismatch, prompt-hash mismatch); tool bodies construct theirs.
    """

    ok: bool
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# A tool body's signature. The dispatcher passes the validated args and the
# principal; bodies do whatever Cosmos / AI Search work they need and return
# the wire envelope.
ToolCallable = Callable[[dict[str, Any], Principal], Awaitable[ToolResult]]


class SessionStore(Protocol):
    """Subset of `CosmosRepository` the dispatcher needs.

    Kept as a `Protocol` rather than a concrete dependency so the
    integration tests (`tests/integration/test_dispatcher_*.py`) can inject
    in-memory fakes without spinning up the Cosmos emulator. The
    production wiring passes a `CosmosRepository` instance — Python
    structural subtyping makes the match implicit.
    """

    async def get_session(self, session_id: str, user_id: str) -> SessionDoc: ...

    async def pause_session(self, session: SessionDoc) -> SessionDoc: ...


class EventEmitter(Protocol):
    """Custom-event sink (App Insights `customEvents` table).

    The dispatcher emits three named events:

      * `agent.dispatch.{tool_name}` — one per dispatch, with `outcome`,
        `latency_ms`, and (for mutex paths) `cache_hit`.
      * `agent.unknown_tool` — rejected tool name only; never the args.
      * `agent.prompt_hash_mismatch` — P0, halts the session.

    A no-op emitter is provided for tests via `_NullEmitter`.
    """

    def emit(self, name: str, properties: Mapping[str, Any]) -> None: ...


class _NullEmitter:
    def emit(self, name: str, properties: Mapping[str, Any]) -> None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# In-process mutex with TTL (TASK-070 step 2)
# ---------------------------------------------------------------------------


@dataclass
class _InflightEntry:
    """One pending or recently-completed tool-body invocation.

    `future` is awaited by every caller after the first; `expires_at` is a
    monotonic-clock deadline at which the entry becomes eligible for
    eviction. A small TTL (60 s) is deliberate — long enough to absorb the
    tail of a slow tool body, short enough that a stuck entry self-heals.
    """

    future: asyncio.Future[ToolResult]
    expires_at: float


class _MutexCache:
    """Async-aware TTL cache keyed by `(session_id, question_id)`.

    Only `submit_answer` writes here; other tools bypass the cache.

    Not exposed beyond this module. Tests that need to inspect contention
    use `Dispatcher._mutex` directly via a friend-of-tests accessor.
    """

    def __init__(self, ttl_seconds: float = _MUTEX_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str], _InflightEntry] = {}
        # One asyncio.Lock guards the dict itself — the slow path is the
        # tool body, which runs **outside** the lock.
        self._guard = asyncio.Lock()

    async def get_or_create(
        self,
        key: tuple[str, str],
    ) -> tuple[asyncio.Future[ToolResult], bool]:
        """Return `(future, is_owner)` — `is_owner=True` for the first caller.

        The first caller is responsible for running the tool body and
        setting the future's result. Subsequent callers await the same
        future and observe whatever result the first caller set.
        """

        async with self._guard:
            self._evict_expired_locked()
            entry = self._entries.get(key)
            if entry is not None and not entry.future.done():
                return entry.future, False
            future: asyncio.Future[ToolResult] = asyncio.get_running_loop().create_future()
            self._entries[key] = _InflightEntry(
                future=future,
                expires_at=time.monotonic() + self._ttl,
            )
            return future, True

    def finalize(
        self,
        key: tuple[str, str],
        result: ToolResult | None,
        exception: BaseException | None,
    ) -> None:
        """Settle the in-flight future and refresh the TTL.

        Called by the owner after the tool body returns (or raises). The
        entry stays in the cache for `ttl_seconds` so a duplicate call
        arriving *just after* the first finishes still gets the cached
        result rather than re-running the body.
        """

        entry = self._entries.get(key)
        if entry is None:
            return
        if not entry.future.done():
            if exception is not None:
                entry.future.set_exception(exception)
            else:
                assert result is not None
                entry.future.set_result(result)
        entry.expires_at = time.monotonic() + self._ttl

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        stale = [k for k, e in self._entries.items() if e.expires_at <= now and e.future.done()]
        for k in stale:
            self._entries.pop(k, None)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher:
    """The MAF-to-tool-body bridge.

    Construction is dependency-injected: `tools` is a name→callable map
    populated by 005-tools; `session_store` is the Cosmos repository (or
    a test fake). `frame_provider` builds the per-session `SessionFrame`
    used for prompt-hash recomputation — pulled out as a callable so the
    dispatcher does not need to know how a SessionDoc maps into the
    invariant subset (that bookkeeping lives in `quiz_agent`).
    """

    def __init__(
        self,
        *,
        tools: Mapping[str, ToolCallable],
        session_store: SessionStore,
        frame_provider: Callable[[SessionDoc], SessionFrame],
        emitter: EventEmitter | None = None,
        clock: Callable[[], float] = time.monotonic,
        mutex: _MutexCache | None = None,
    ) -> None:
        missing = ALLOWED_TOOLS - tools.keys()
        if missing:
            raise FlintValidationError(
                f"dispatcher missing tool implementations: {sorted(missing)}"
            )
        extras = tools.keys() - ALLOWED_TOOLS
        if extras:
            # Defence-in-depth: even if a contributor manages to register a
            # sixth tool on the agent, the dispatcher refuses to accept it
            # at construction time.
            raise FlintValidationError(
                f"dispatcher refusing to register tools outside the allowlist: "
                f"{sorted(extras)}"
            )
        self._tools: dict[str, ToolCallable] = dict(tools)
        self._session_store = session_store
        self._frame_provider = frame_provider
        self._emitter = emitter or _NullEmitter()
        self._clock = clock
        self._mutex = mutex or _MutexCache()

    # ----- Public entrypoint ------------------------------------------------

    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        principal: Principal,
    ) -> ToolResult:
        """Validate, route, and observe a single tool-call request."""

        start = self._clock()

        # (a) Allowlist — fail closed before any side effects.
        if tool_name not in ALLOWED_TOOLS:
            # GOV-010 P1. The event payload is **only** the rejected name
            # — args are excluded because they may contain user content
            # the model was attempting to weaponise (audit §5.8 / GOV-063).
            self._emitter.emit(
                "agent.unknown_tool",
                {"rejected_name": tool_name},
            )
            return _internal_error(
                code="E_UNKNOWN_TOOL",
                message_user_key="refusal_off_topic",
            )

        # (b) Tool-arg impersonation defence (GOV-063 / audit §5.8). The
        # `user_id` field, when present in the args, must match the
        # authenticated principal. We do **not** silently swap the value —
        # a mismatch is a contract violation and the call is rejected.
        if "user_id" in args and args["user_id"] != principal.entra_oid:
            self._emitter.emit(
                "agent.auth_mismatch",
                {
                    "tool": tool_name,
                    "principal_oid_prefix": _short_oid(principal.entra_oid),
                },
            )
            return _auth_mismatch()

        # (c) Per-session prompt-hash verification (GOV-001..003).
        if tool_name in _SESSION_BOUND_TOOLS and "session_id" in args:
            verdict = await self._verify_prompt_hash(args, principal, tool_name)
            if verdict is not None:
                return verdict

        # (d) Mutex-aware execution (GOV-012 for submit_answer; other
        # tools take the straight path).
        try:
            if tool_name in _MUTEX_TOOLS:
                result, cache_hit = await self._invoke_with_mutex(tool_name, args, principal)
            else:
                result = await self._tools[tool_name](args, principal)
                cache_hit = False
        except FlintError as exc:
            # Domain exceptions are translated to the wire envelope. Internal
            # codes never reach the LLM (008-api §6.4); we log the typed
            # exception here.
            logger.exception(
                "dispatch.domain_error",
                extra={"tool": tool_name, "error_type": type(exc).__name__},
            )
            result = _from_flint_error(exc)
            cache_hit = False
        except Exception:  # noqa: BLE001 — log + surface generic envelope
            logger.exception("dispatch.unexpected_error", extra={"tool": tool_name})
            result = _internal_error(code="E_INTERNAL", message_user_key="refusal_off_topic")
            cache_hit = False

        latency_ms = int((self._clock() - start) * 1000)
        self._emitter.emit(
            f"agent.dispatch.{tool_name}",
            {
                "outcome": "ok" if result.ok else (result.error or {}).get("code", "error"),
                "latency_ms": latency_ms,
                "cache_hit": cache_hit,
            },
        )
        return result

    # ----- Internals --------------------------------------------------------

    async def _verify_prompt_hash(
        self,
        args: dict[str, Any],
        principal: Principal,
        tool_name: str,
    ) -> ToolResult | None:
        """Re-compose the prompt and assert equality with the persisted hash.

        Returns `None` on success (caller continues). Returns a non-None
        `ToolResult` on every failure path — the dispatcher surfaces it
        immediately without invoking the tool body.

        `start_quiz` does **not** have a session yet, so verification is
        skipped on that path; the tool body is responsible for writing
        the initial hash. Other session-bound tools must find an existing
        `prompt_hash` on the row.
        """

        if tool_name == "start_quiz":
            return None  # no session yet; tool body initialises the hash

        session_id = args["session_id"]
        try:
            session = await self._session_store.get_session(session_id, principal.entra_oid)
        except FlintNotFoundError:
            return ToolResult(
                ok=False,
                error={
                    "code": "E_SESSION_NOT_FOUND",
                    "message_user_key": "refusal_off_topic",
                },
            )

        # Sessions created before TASK-071 carry no hash. Treat that as
        # P0 — we never want a session to skip the verification step in
        # the post-004 world. The runtime explicitly refuses.
        if not session.prompt_hash:
            self._emitter.emit(
                "agent.prompt_hash_missing",
                {"session_id": session.id, "tool": tool_name},
            )
            return ToolResult(
                ok=False,
                error={
                    "code": "E_SESSION_PAUSED",
                    "message_user_key": "refusal_off_topic",
                },
            )

        frame = self._frame_provider(session)
        _, computed = compose(language=session.language, session_frame=frame)
        if computed != session.prompt_hash:
            # P0 escalation. The session is paused so that subsequent
            # calls do not retry against a tampered prompt; on-call is
            # paged via the event sink (008-observability).
            self._emitter.emit(
                "agent.prompt_hash_mismatch",
                {
                    "session_id": session.id,
                    "expected_prefix": _short_hash(session.prompt_hash),
                    "actual_prefix": _short_hash(computed),
                    "tool": tool_name,
                },
            )
            try:
                if session.status != SessionStatus.PAUSED.value:
                    await self._session_store.pause_session(session)
            except SessionStateError:
                # Already in a terminal state; the pause attempt was best-
                # effort. The hash-mismatch event is the load-bearing log.
                logger.warning(
                    "prompt_hash_mismatch.pause_skipped",
                    extra={"session_id": session.id, "current_status": session.status},
                )
            return ToolResult(
                ok=False,
                error={
                    "code": "E_SESSION_PAUSED",
                    "message_user_key": "refusal_off_topic",
                    "incident": "PROMPT_HASH_MISMATCH",
                },
            )

        return None

    async def _invoke_with_mutex(
        self,
        tool_name: str,
        args: dict[str, Any],
        principal: Principal,
    ) -> tuple[ToolResult, bool]:
        """Run the tool body under the per-`(session_id, question_id)` mutex.

        Returns `(result, cache_hit)`. `cache_hit=True` indicates the
        caller awaited a future owned by another coroutine — exactly the
        scenario the test in `tests/integration/test_dispatcher_mutex.py`
        exercises.
        """

        try:
            key = (str(args["session_id"]), str(args["question_id"]))
        except KeyError as exc:
            return (
                ToolResult(
                    ok=False,
                    error={"code": "E_INVALID_INPUT", "message_user_key": "refusal_off_topic"},
                ),
                False,
            )

        future, is_owner = await self._mutex.get_or_create(key)
        if not is_owner:
            try:
                result = await future
            except BaseException as exc:  # noqa: BLE001
                # The owner reported a failure; surface the same shape so
                # both callers observe one outcome (008-api §4.4 idempotency).
                logger.info(
                    "dispatch.mutex.shared_failure",
                    extra={
                        "tool": tool_name,
                        "session_id": key[0],
                        "question_id": key[1],
                        "error_type": type(exc).__name__,
                    },
                )
                result = _from_flint_error(
                    exc if isinstance(exc, FlintError) else FlintValidationError(str(exc))
                )
            return result, True

        # Owner path. Run the tool body OUTSIDE the cache guard, then
        # settle the future for any waiters.
        try:
            result = await self._tools[tool_name](args, principal)
        except BaseException as exc:  # noqa: BLE001
            self._mutex.finalize(key, None, exc)
            raise
        else:
            self._mutex.finalize(key, result, None)
        return result, False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _internal_error(*, code: str, message_user_key: str) -> ToolResult:
    return ToolResult(
        ok=False,
        error={"code": code, "message_user_key": message_user_key},
    )


def _auth_mismatch() -> ToolResult:
    return ToolResult(
        ok=False,
        error={"code": "E_AUTH_MISMATCH", "message_user_key": "refusal_off_topic"},
    )


def _from_flint_error(exc: BaseException) -> ToolResult:
    # Map domain exceptions to the wire envelope. The mapping mirrors
    # 008-api §6.3 / §6.4. Unknown exception types fall through to the
    # generic E_INTERNAL — by design, no internal exception text reaches
    # the LLM.
    if isinstance(exc, AnswerLeakageError):
        # SEC-001 P0. Surface a generic envelope; the real signal is the
        # event the caller already emitted.
        return _internal_error(code="E_INTERNAL", message_user_key="refusal_off_topic")
    if isinstance(exc, FlintAuthorizationError):
        return _auth_mismatch()
    if isinstance(exc, FlintNotFoundError):
        return ToolResult(
            ok=False,
            error={
                "code": "E_SESSION_NOT_FOUND",
                "message_user_key": "refusal_off_topic",
            },
        )
    if isinstance(exc, SessionStateError):
        return ToolResult(
            ok=False,
            error={
                "code": "E_SESSION_NOT_ACTIVE",
                "message_user_key": "refusal_off_topic",
                "from_status": exc.from_status,
                "to_status": exc.to_status,
            },
        )
    if isinstance(exc, FlintValidationError):
        return ToolResult(
            ok=False,
            error={"code": "E_INVALID_INPUT", "message_user_key": "refusal_off_topic"},
        )
    return _internal_error(code="E_INTERNAL", message_user_key="refusal_off_topic")


def _short_hash(value: str) -> str:
    # First 12 hex chars are enough to triage a mismatch and short enough
    # to fit any log surface. Full hashes are reconstructable from the
    # Cosmos row and the Blob-stored layers; the event need not carry both.
    return (value or "")[:12]


def _short_oid(value: str) -> str:
    return (value or "")[:8]


__all__ = [
    "ALLOWED_TOOLS",
    "Dispatcher",
    "EventEmitter",
    "Principal",
    "SessionStore",
    "ToolCallable",
    "ToolResult",
]
