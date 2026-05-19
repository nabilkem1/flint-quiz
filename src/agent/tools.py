"""The five tools the agent calls (TASK-081..085 / 005-tools).

Every tool here:

  1. Validates inputs through a Pydantic request model with
     ``extra="forbid"`` (008-api §4.1).
  2. Returns a typed Pydantic response model — never a free-form dict.
  3. Runs the recursive ``strip_answer_key`` (TASK-088) over its payload
     before constructing the :class:`ToolResult`. The strip is the third
     line of defence; the load-bearing protection is the typed
     ``QuestionView`` projection (see ``src/data/question_search.py``).
  4. Goes through the dispatcher (``src/agent/dispatcher.py``) — never
     called directly from MAF's loop or any other module. The
     ``import-linter`` contract in ``pyproject.toml`` enforces this.

The **only** function in this module that touches ``get_answer_key`` is
:func:`submit_answer`. An AST check (``tests/integration/test_question_search.py``)
walks this module's tree and fails the build if any other function names
the symbol. The import lives **inside** ``submit_answer``'s body for the
same reason.

Tool dependencies (Cosmos repo, AI Search client, AppConfig, clock,
event emitter) are injected via :class:`ToolDeps` and the
:func:`build_tools` factory. Production wiring lives in
``src/agent/quiz_agent.py``; tests construct lightweight fakes.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from src.agent.answer_normalizer import normalize_answer
from src.agent.coverage_fallback import suggest_fallback
from src.agent.defensive_strip import strip_answer_key
from src.agent.dispatcher import Principal, ToolCallable, ToolResult
from src.agent.timers import TimerVerdict, evaluate_timers, utc_now
from src.agent.tts_shaper import shape_question, shape_text
from src.common.exceptions import (
    FlintAuthorizationError,
    FlintError,
    FlintNotFoundError,
    FlintUpstreamError,
    FlintValidationError,
    InvalidLanguageError,
    SessionStateError,
)
from src.data.cosmos_repository import CosmosRepository
from src.data.models import (
    SUPPORTED_LANGUAGES,
    Answer,
    AuditEvent,
    BreakdownItem,
    Channel,
    FallbackNotice,
    GetResultsRequest,
    GetResultsResponse,
    ListTopicsRequest,
    ListTopicsResponse,
    Option,
    QuestionView,
    ResultsSummary,
    SessionDoc,
    SessionStatus,
    SetLanguageRequest,
    SetLanguageResponse,
    StartQuizRequest,
    StartQuizResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
    TopicSummary,
    UserDoc,
    Verdict,
)
from src.data.question_search import QuestionSearch
from src.data.shuffle import compute_seed, derive_shuffled_ids

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Fallback question count when neither `start_quiz`'s `n` arg nor the
# topic's `default_n` is set. 10 matches the historical seed (each topic
# has ~10 questions per language) so a default-everything quiz uses the
# whole bank.
START_QUIZ_DEFAULT_N: int = 10


# ---------------------------------------------------------------------------
# Dependency container
# ---------------------------------------------------------------------------


class EventEmitter(Protocol):
    """Tool-side event sink. Mirrors the dispatcher's protocol shape.

    Implemented in 008-observability; tests pass a list-backed fake.
    """

    def emit(self, name: str, properties: Mapping[str, Any]) -> None: ...


class _NullEmitter:
    def emit(self, name: str, properties: Mapping[str, Any]) -> None:  # pragma: no cover
        return None


@dataclass(frozen=True, slots=True)
class ToolDeps:
    """Dependency surface for the five tool functions.

    Constructed once per agent process and reused across requests. The
    ``allowlist_provider`` is a callable so the live allowlist is refreshed
    from AppConfig without a process restart (007-security TASK-123). The
    ``clock`` indirection lets tests pin time without monkeypatching the
    ``datetime`` module globally.
    """

    repo: CosmosRepository
    search: QuestionSearch
    allowlist_provider: Callable[[], frozenset[str]] = lambda: SUPPORTED_LANGUAGES
    pass_threshold_pct: float = 60.0
    per_question_limit_seconds: int = 60
    default_time_limit_seconds: int = 600
    emitter: EventEmitter = field(default_factory=_NullEmitter)
    clock: Callable[[], datetime] = utc_now


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_tools(deps: ToolDeps) -> dict[str, ToolCallable]:
    """Return the five tools wired against `deps`.

    The dispatcher validates that the returned dict's keys equal
    :data:`src.agent.dispatcher.ALLOWED_TOOLS` (the registration check
    fails closed otherwise). Wiring lives in ``quiz_agent.py``.

    The module-level body for the answer-key path is named
    :func:`submit_answer` so the AST lint in
    ``tests/integration/test_question_search.py`` records every
    ``get_answer_key`` reference against the only sanctioned scope.
    """

    from functools import partial

    return {
        "list_topics": partial(_list_topics, deps=deps),  # type: ignore[dict-item]
        "set_language": partial(_set_language, deps=deps),  # type: ignore[dict-item]
        "start_quiz": partial(_start_quiz, deps=deps),  # type: ignore[dict-item]
        "submit_answer": partial(submit_answer, deps=deps),  # type: ignore[dict-item]
        "get_results": partial(_get_results, deps=deps),  # type: ignore[dict-item]
    }


# ---------------------------------------------------------------------------
# Tool bodies
# ---------------------------------------------------------------------------


async def _list_topics(
    args: dict[str, Any], principal: Principal, deps: ToolDeps
) -> ToolResult:
    """Return the catalog of available topics with localised labels.

    Topics with zero question count in the requested language are omitted
    from the returned list and instead carry `has_fallback=True` on rows
    whose language coverage exists elsewhere (the agent can offer to switch
    via the consent flow).
    """

    try:
        request = ListTopicsRequest.model_validate(args)
    except Exception as exc:  # noqa: BLE001 — translate to wire envelope
        return _validation_error(str(exc))

    if (err := _ensure_language_allowed(request.language, deps)) is not None:
        return err

    topics = await deps.repo.list_topics()
    summaries: list[TopicSummary] = []
    for topic in topics:
        if not topic.enabled:
            continue
        label = topic.labels.get(request.language) or topic.labels.get(
            topic.default_language, topic.topic_id
        )
        count = int(topic.counts.get(request.language, 0))
        has_fallback = count == 0 and any(c > 0 for c in topic.counts.values())
        if count == 0 and not has_fallback:
            # No coverage anywhere — omit from the catalog.
            continue
        summaries.append(
            TopicSummary(
                topic_id=topic.topic_id,
                label=label,
                count=count,
                has_fallback=has_fallback,
            )
        )

    response = ListTopicsResponse(language=request.language, topics=summaries)
    return _ok(response)


async def _set_language(
    args: dict[str, Any], principal: Principal, deps: ToolDeps
) -> ToolResult:
    try:
        request = SetLanguageRequest.model_validate(args)
    except Exception as exc:  # noqa: BLE001
        return _validation_error(str(exc))

    if request.user_id != principal.entra_oid:
        raise FlintAuthorizationError("set_language: user_id does not match principal")

    if (err := _ensure_language_allowed(request.language, deps)) is not None:
        return err

    now = deps.clock()
    existing = await deps.repo.get_user(request.user_id)
    if existing is None:
        user = UserDoc(
            id=request.user_id,
            user_id=request.user_id,
            language=request.language,
            detected_language=request.language,
            explicitly_set=True,
            created_at=now,
            updated_at=now,
        )
    else:
        user = existing.model_copy(
            update={
                "language": request.language,
                "explicitly_set": True,
                "updated_at": now,
            }
        )
    stored = await deps.repo.upsert_user(user)
    response = SetLanguageResponse(
        user_id=stored.user_id,
        language=stored.language,
        updated_at=stored.updated_at,
    )
    return _ok(response)


async def _start_quiz(
    args: dict[str, Any], principal: Principal, deps: ToolDeps
) -> ToolResult:
    """Create a session, seed the shuffle, return Q1 (no `correct_answer`).

    Coverage decision tree (per the task pack prompt):
      * count[lang] == 0  → `E_NO_COVERAGE` with `suggested_fallback`; the
        agent runs the consent flow (TASK-189). No silent switch.
      * count[lang] < n   → clamp `n`; populate `fallback_notice` with
        `reason="count_clamped"`. Language is **not** changed.
      * count[lang] >= n  → proceed normally.
    """

    try:
        request = StartQuizRequest.model_validate(args)
    except Exception as exc:  # noqa: BLE001
        return _validation_error(str(exc))

    if request.user_id != principal.entra_oid:
        raise FlintAuthorizationError("start_quiz: user_id does not match principal")

    if (err := _ensure_language_allowed(request.language, deps)) is not None:
        return err

    topic = await deps.repo.get_topic(request.topic)
    if topic is None or not topic.enabled:
        return _error(
            code="E_UNKNOWN_TOPIC",
            message_user_key="refusal_off_topic",
            detail={"topic": request.topic},
        )

    # Resolve effective n: user-supplied wins, then the topic's
    # configured default, then the module-level constant. This is the
    # quiz-definition-default flow — operator pre-configures `default_n`
    # in `topics.json`, the seed loader writes it to the Cosmos `topics`
    # container, and start_quiz picks it up here whenever the model
    # doesn't pass an explicit `n`.
    requested_n: int = (
        request.n
        if request.n is not None
        else (topic.default_n if topic.default_n is not None else START_QUIZ_DEFAULT_N)
    )

    coverage = int(topic.counts.get(request.language, 0))
    fallback_notice: FallbackNotice | None = None

    if coverage == 0:
        user = await deps.repo.get_user(request.user_id)
        suggested = suggest_fallback(
            topic,
            requested_lang=request.language,
            n=requested_n,
            user_preferred=(user.language if user else None),
        )
        return _error(
            code="E_NO_COVERAGE",
            message_user_key="coverage_gap_consent",
            detail={
                "requested": request.language,
                "topic": request.topic,
                "suggested_fallback": suggested,
            },
        )

    effective_n = requested_n
    if coverage < requested_n:
        effective_n = coverage
        fallback_notice = FallbackNotice(
            requested=request.language,
            resolved=request.language,
            reason="count_clamped",
            requested_n=requested_n,
            resolved_n=effective_n,
        )

    # Filtered candidate ID draw — AI Search filters by language explicitly.
    candidate_ids = await deps.search.search_topic(
        request.topic,
        language=request.language,  # type: ignore[arg-type]
        difficulty=None if request.difficulty in (None, "mixed") else request.difficulty,
        top=max(effective_n * 2, effective_n),
    )
    if len(candidate_ids) == 0:
        # Topic counts said >0 but the live index returned 0 — surface as
        # a transient backend issue rather than silently lying about coverage.
        return _error(
            code="E_BACKEND_TRANSIENT",
            message_user_key="refusal_off_topic",
            detail={"topic": request.topic, "language": request.language},
            retryable=True,
        )

    # Deterministic shuffle + truncate to `effective_n` (NFR-003).
    session_id = str(uuid.uuid4())
    seed = compute_seed(session_id)
    shuffled = derive_shuffled_ids(seed, [str(cid) for cid in candidate_ids])
    selected_ids = shuffled[:effective_n]

    now = deps.clock()
    session_doc = SessionDoc(
        id=session_id,
        user_id=request.user_id,
        topic=request.topic,
        language=request.language,
        requested_language=request.language,
        seed=seed,
        shuffled_ids=selected_ids,
        current_index=0,
        answers=[],
        score=0.0,
        max_score=float(effective_n),
        status=SessionStatus.ACTIVE,
        started_at=now,
        question_started_at=now,
        time_limit_seconds=request.time_limit_seconds,
        per_question_limit_seconds=deps.per_question_limit_seconds,
        pass_threshold_pct=deps.pass_threshold_pct,
        channel=request.channel,
    )
    stored = await deps.repo.create_session(session_doc)

    first_question_id = stored.shuffled_ids[0]
    first_view = await deps.search.get_question_view(first_question_id)  # type: ignore[arg-type]
    shaped_question = _shape_question_view(first_view, request.language)

    response = StartQuizResponse(
        session_id=stored.id,
        question=shaped_question,
        index=1,
        total=len(stored.shuffled_ids),
        language=stored.language,
        fallback_notice=fallback_notice,
        time_limit_seconds=stored.time_limit_seconds,
        question_started_at=stored.question_started_at,
    )
    return _ok(response)


async def submit_answer(
    args: dict[str, Any], principal: Principal, deps: ToolDeps
) -> ToolResult:
    """Grade, persist via conditional write, return next question.

    This is the **only** function in this module permitted to read the
    answer key. The :func:`QuestionSearch.get_answer_key` access lives in
    this function's body so the AST visitor in
    ``tests/integration/test_question_search.py`` records the reference
    against scope name ``submit_answer``.
    """

    try:
        request = SubmitAnswerRequest.model_validate(args)
    except Exception as exc:  # noqa: BLE001
        return _validation_error(str(exc))

    session = await deps.repo.get_session(request.session_id, principal.entra_oid)

    if session.user_id != principal.entra_oid:
        raise FlintAuthorizationError("submit_answer: caller does not own session")

    if SessionStatus(session.status) != SessionStatus.ACTIVE:
        raise SessionStateError(
            f"submit_answer rejected: session {session.id} is {session.status}",
            from_status=str(session.status),
            to_status=SessionStatus.ACTIVE.value,
        )

    # Server-side timer enforcement (NFR-004 / FR-015). Order matters: a
    # per-quiz expiry supersedes a per-question expiry — flip to Expired
    # and return the final results envelope.
    timer = evaluate_timers(session, clock=deps.clock)
    if timer.verdict == TimerVerdict.QUIZ_EXPIRED:
        expired = await deps.repo.expire_session(session)
        scored = await deps.repo.score_session(expired)
        results = _build_results_summary(scored, deps)
        response = SubmitAnswerResponse(
            verdict=Verdict.UNANSWERED,
            score_delta=0.0,
            running_score=scored.score,
            index=min(timer_index(scored), len(scored.shuffled_ids)),
            total=len(scored.shuffled_ids),
            next=None,
            done=True,
            results=results,
            question_started_at=None,
        )
        return _ok(response)

    expected_qid = (
        session.shuffled_ids[session.current_index]
        if session.current_index < len(session.shuffled_ids)
        else None
    )
    if request.question_id != expected_qid:
        # The idempotent-replay path: if the supplied question was already
        # graded, replay its verdict instead of erroring.
        replay = next(
            (a for a in session.answers if a.question_id == request.question_id),
            None,
        )
        if replay is not None:
            return _ok(_replay_response(session, replay, deps))
        return _error(
            code="E_QUESTION_OUT_OF_ORDER",
            message_user_key="refusal_off_topic",
            detail={
                "expected": expected_qid,
                "received": request.question_id,
            },
        )

    # Per-question expiry → grade as `unanswered`; do NOT error.
    if timer.verdict == TimerVerdict.QUESTION_EXPIRED:
        answer = Answer(
            question_id=request.question_id,
            received_raw=request.raw_answer,
            received_normalized=None,
            verdict=Verdict.UNANSWERED,
            score_delta=0.0,
            answered_at=deps.clock(),
            channel=request.channel,
            latency_ms=timer.question_elapsed_seconds * 1000,
        )
        return await _finalise_answer(
            session=session,
            answer=answer,
            normalized=None,
            expected=frozenset(),
            request=request,
            deps=deps,
        )

    # Fetch the question's options (for ordinal / option_text normalisation).
    # `accept_multi=True` is used unconditionally — the grader (set comparison
    # for multi-correct, `==` for single-correct) is the final arbiter. A
    # user who says "A and B" on a single-correct question gets `incorrect`
    # via the equality branch rather than an opaque ambiguity envelope.
    view = await deps.search.get_question_view(request.question_id)  # type: ignore[arg-type]
    normalised = normalize_answer(
        request.raw_answer,
        language=session.language,
        options=view.options,
        accept_multi=True,
    )

    if normalised.matched is None and normalised.ambiguous:
        return _error(
            code="E_NORMALIZER_AMBIGUOUS",
            message_user_key="refusal_off_topic",
            detail={"strategy": normalised.strategy},
        )

    # Server-only path. The literal call to `get_answer_key` lives inside
    # this function body — that is the AST-enforced SEC-002 boundary. The
    # `AnswerKey` instance has no JSON serializer and a redacted __repr__.
    answer_key = await deps.search.get_answer_key(request.question_id)  # type: ignore[arg-type]
    expected: frozenset[str] = answer_key.correct
    score_weight = float(answer_key.score_weight)

    verdict, score_delta = _grade(normalised.matched, expected, score_weight)

    now = deps.clock()
    answered_at = now
    latency_ms = int(max(0, (now - session.question_started_at).total_seconds() * 1000))
    answer = Answer(
        question_id=request.question_id,
        received_raw=request.raw_answer,
        received_normalized=(
            normalised.matched
            if normalised.matched is not None and len(normalised.matched) > 1
            else (normalised.matched[0] if normalised.matched else None)
        ),
        verdict=verdict,
        score_delta=score_delta,
        answered_at=answered_at,
        channel=request.channel,
        latency_ms=latency_ms,
    )

    return await _finalise_answer(
        session=session,
        answer=answer,
        normalized=normalised.matched,
        expected=expected,
        request=request,
        deps=deps,
    )


async def _get_results(
    args: dict[str, Any], principal: Principal, deps: ToolDeps
) -> ToolResult:
    try:
        request = GetResultsRequest.model_validate(args)
    except Exception as exc:  # noqa: BLE001
        return _validation_error(str(exc))

    if request.user_id != principal.entra_oid:
        raise FlintAuthorizationError("get_results: user_id does not match principal")

    session = await deps.repo.get_session(request.session_id, principal.entra_oid)
    status = SessionStatus(session.status)

    if status in (SessionStatus.ACTIVE, SessionStatus.PAUSED):
        return _error(
            code="E_SESSION_NOT_FINAL",
            message_user_key="refusal_off_topic",
            detail={"status": status.value},
        )

    if status in (SessionStatus.COMPLETED, SessionStatus.EXPIRED):
        session = await deps.repo.score_session(session)

    summary = _build_results_summary(session, deps)
    return _ok(summary)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grade(
    normalized: list[str] | None,
    expected: frozenset[str],
    score_weight: float,
) -> tuple[Verdict, float]:
    """Deterministic grading (008-api §1.6.4).

    Multi-correct keys are compared as sets; single-correct collapses to
    an equality check on the lone key. A `None` normalised value means the
    user said something that did not map to a key — verdict is `unanswered`
    with zero score.
    """

    if normalized is None:
        return Verdict.UNANSWERED, 0.0
    got = frozenset(k.upper() for k in normalized)
    if len(expected) > 1:
        if got == expected:
            return Verdict.CORRECT, score_weight
        if got and got.issubset(expected):
            return Verdict.PARTIAL, score_weight * (len(got) / len(expected))
        return Verdict.INCORRECT, 0.0
    # single-correct
    if got == expected:
        return Verdict.CORRECT, score_weight
    return Verdict.INCORRECT, 0.0


async def _finalise_answer(
    *,
    session: SessionDoc,
    answer: Answer,
    normalized: list[str] | None,
    expected: frozenset[str],
    request: SubmitAnswerRequest,
    deps: ToolDeps,
) -> ToolResult:
    """Persist the answer and build the response.

    The `grading_event` (008-api §4.5) fires **only** on the successful
    conditional-write branch (`persisted=True`). Idempotent no-ops do not
    emit, by design — TEST-007 asserts the metric is incremented exactly
    once per persisted answer.
    """

    updated, persisted = await deps.repo.append_answer_conditional(session, answer)

    if persisted:
        await _emit_grading(updated, answer, normalized, expected, deps)

    running_score = updated.score
    total = len(updated.shuffled_ids)
    index_just_graded = updated.current_index  # already advanced by repo

    done = SessionStatus(updated.status) in (
        SessionStatus.COMPLETED,
        SessionStatus.EXPIRED,
        SessionStatus.SCORED,
    )

    next_question: QuestionView | None = None
    next_question_started_at: datetime | None = None
    results: ResultsSummary | None = None

    if done:
        if SessionStatus(updated.status) == SessionStatus.COMPLETED:
            updated = await deps.repo.score_session(updated)
        results = _build_results_summary(updated, deps)
    else:
        next_qid = updated.shuffled_ids[updated.current_index]
        next_view = await deps.search.get_question_view(next_qid)  # type: ignore[arg-type]
        next_question = _shape_question_view(next_view, updated.language)
        next_question_started_at = updated.question_started_at

    response = SubmitAnswerResponse(
        verdict=Verdict(answer.verdict),
        score_delta=answer.score_delta,
        running_score=running_score,
        index=index_just_graded,
        total=total,
        next=next_question,
        done=done,
        results=results,
        question_started_at=next_question_started_at,
    )
    return _ok(response)


async def _emit_grading(
    session: SessionDoc,
    answer: Answer,
    normalized: list[str] | None,
    expected: frozenset[str],
    deps: ToolDeps,
) -> None:
    """Emit the App Insights grading_event and write the audit row.

    The two sinks carry **different** shapes (008-api §4.5):
      * App Insights `grading_event`: NO `expected`, NO `received_raw`.
      * Cosmos `audit` row: contains both (RBAC-restricted; system of record).

    A failure to write the audit row is logged but does NOT fail the
    grading path — by the time we are here, the answer has already been
    persisted and the user-facing result is correct.
    """

    received_token = (
        ",".join(normalized) if normalized else str(answer.received_normalized or "")
    )

    # Route through the typed emitter so the dimension policy
    # (008-observability TASK-141 / `src/observability/events.py`)
    # enforces the SEC-001 boundary on every emission. The policy
    # rejects `expected`, `received_raw`, `correct_answer`, etc. at the
    # source — a future contributor adding a "convenient" field hits a
    # build break before runtime.
    from src.observability.events import emit_grading_event  # noqa: PLC0415 - lazy

    emit_grading_event(
        deps.emitter,
        session_id=session.id,
        question_id=answer.question_id,
        user_id=session.user_id,
        language=session.language,
        received=received_token,
        verdict=(
            answer.verdict.value if isinstance(answer.verdict, Verdict) else answer.verdict
        ),
        channel=(
            answer.channel.value if isinstance(answer.channel, Channel) else answer.channel
        ),
        score_delta=answer.score_delta,
        latency_ms=answer.latency_ms,
        timestamp=answer.answered_at.isoformat() if hasattr(answer.answered_at, "isoformat") else str(answer.answered_at),
    )

    try:
        await deps.repo.write_audit_event(
            AuditEvent(
                id=str(uuid.uuid4()),
                session_id=session.id,
                user_id=session.user_id,
                question_id=answer.question_id,
                language=session.language,
                channel=answer.channel,
                expected=sorted(expected),
                received=received_token,
                received_raw=answer.received_raw,
                verdict=Verdict(answer.verdict)
                if not isinstance(answer.verdict, Verdict)
                else answer.verdict,
                score_delta=answer.score_delta,
                latency_ms=answer.latency_ms,
                timestamp=answer.answered_at,
            )
        )
    except FlintUpstreamError:
        logger.warning(
            "submit_answer.audit_write_failed",
            extra={"session_id": session.id, "question_id": answer.question_id},
        )


def _build_results_summary(session: SessionDoc, deps: ToolDeps) -> ResultsSummary:
    """Compute the final results envelope from a Scored/Expired session.

    Per the task pack prompt: the breakdown carries `{question_id, verdict,
    score}` per row — never question text, never the answer key. Pass/fail
    determination uses the session's per-row threshold (defaults to
    AppConfig's `scoring:defaultPassThresholdPct`, 60%).
    """

    max_score = session.max_score if session.max_score > 0 else 1.0
    percentage = round((session.score / max_score) * 100.0, 2)
    threshold = float(session.pass_threshold_pct or deps.pass_threshold_pct)
    is_pass = percentage >= threshold

    breakdown: list[BreakdownItem] = []
    for answer in session.answers:
        breakdown.append(
            BreakdownItem(
                question_id=answer.question_id,
                verdict=Verdict(answer.verdict)
                if not isinstance(answer.verdict, Verdict)
                else answer.verdict,
                score=answer.score_delta,
            )
        )

    duration_seconds = 0
    if session.answers:
        last_answered = max(_aware(a.answered_at) for a in session.answers)
        duration_seconds = max(
            0, int((last_answered - _aware(session.started_at)).total_seconds())
        )

    return ResultsSummary(
        session_id=session.id,
        status=SessionStatus(session.status),
        score=session.score,
        max_score=session.max_score,
        percentage=percentage,
        is_pass=is_pass,
        pass_threshold_pct=threshold,
        language=session.language,
        duration_seconds=duration_seconds,
        breakdown=breakdown,
    )


def _replay_response(
    session: SessionDoc, replay: Answer, deps: ToolDeps
) -> SubmitAnswerResponse:
    """Build the replay envelope for a duplicate `submit_answer` (008-api §1.6.6)."""

    total = len(session.shuffled_ids)
    status = SessionStatus(session.status)
    done = status in (SessionStatus.COMPLETED, SessionStatus.EXPIRED, SessionStatus.SCORED)
    results = _build_results_summary(session, deps) if done else None
    return SubmitAnswerResponse(
        verdict=Verdict(replay.verdict)
        if not isinstance(replay.verdict, Verdict)
        else replay.verdict,
        score_delta=replay.score_delta,
        running_score=session.score,
        index=session.current_index,
        total=total,
        next=None,
        done=done,
        results=results,
        question_started_at=None,
    )


def _shape_question_view(view: QuestionView, language: str) -> QuestionView:
    """Return a TTS-shaped copy of the question without widening the schema.

    The shaper rewrites `text` and option `text` to be voice-safe; the
    field set is unchanged so the `QuestionView` boundary still bars
    `correct_answer` from sneaking through.
    """

    shaped_text = shape_text(view.text, language=language)
    shaped_options = [
        Option(key=opt.key, text=shape_text(opt.text, language=language))
        for opt in view.options
    ]
    return QuestionView(
        question_id=view.question_id,
        text=shaped_text,
        options=shaped_options,
        difficulty=view.difficulty,
    )


def _ensure_language_allowed(language: str, deps: ToolDeps) -> ToolResult | None:
    """Refuse a language code that is not in the live AppConfig allowlist.

    Returns `None` on success; an `E_INVALID_LANGUAGE` envelope on failure.
    Callers check the return and short-circuit. The validator is a callable
    so the live allowlist can change without a process restart (007-security
    TASK-123); `SUPPORTED_LANGUAGES` is the build-time fallback.
    """

    allowlist = deps.allowlist_provider()
    if language not in allowlist:
        return _error(
            code="E_INVALID_LANGUAGE",
            message_user_key="refusal_off_topic",
            detail={"language": language, "allowlist": sorted(allowlist)},
        )
    return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def timer_index(session: SessionDoc) -> int:
    """Best-effort index for the `index` field on an expired-quiz response."""

    return max(1, min(session.current_index, len(session.shuffled_ids)))


# ---------------------------------------------------------------------------
# Result builders + envelope helpers
# ---------------------------------------------------------------------------


def _ok(model: Any) -> ToolResult:
    """Wrap a Pydantic response model into a defensively-stripped `ToolResult`.

    `by_alias=True` is required so the `ResultsSummary.is_pass` field
    serialises as the spec's `pass` key (008-api §1.7). Other tool models
    have no aliases, so `by_alias=True` is a no-op there.

    Every payload passes through `strip_answer_key` even though the
    response model is `extra="forbid"`. This is the third line of defence:
    a future widening or a tainted record reaching the boundary surfaces
    as a warning (the strip itself never errors).
    """

    if hasattr(model, "model_dump"):
        payload = model.model_dump(mode="json", by_alias=True)
    else:
        payload = dict(model)
    cleaned, _found = strip_answer_key(payload)
    return ToolResult(ok=True, data=cleaned)


def _error(
    *,
    code: str,
    message_user_key: str,
    detail: dict[str, Any] | None = None,
    retryable: bool = False,
) -> ToolResult:
    """Construct the wire-level error envelope (008-api §4.2)."""

    error: dict[str, Any] = {
        "code": code,
        "message_user_key": message_user_key,
        "retryable": retryable,
    }
    if detail is not None:
        error["detail"] = detail
    return ToolResult(ok=False, error=error)


def _validation_error(detail: str) -> ToolResult:
    """Translate a Pydantic ValidationError into the wire envelope.

    The raw Pydantic message is `message_dev` (server-only telemetry); the
    LLM only sees `message_user_key` via the rendering layer.
    """

    return _error(
        code="E_INVALID_INPUT",
        message_user_key="refusal_off_topic",
        detail={"validation": detail},
    )


__all__ = ["ToolDeps", "build_tools", "EventEmitter"]
