"""Domain models for the AI Search question bank (002 task pack).

Two boundaries cross this module:

1. **Seed / authoring boundary** — `Question` validates the JSON-on-disk shape
   under `src/seed/questions/{lang}/<topic>/<logical_id>.json`. The seed loader
   refuses to upsert any record that fails this validation, so authoring drift
   surfaces at seed time rather than at runtime.

2. **LLM boundary** — `QuestionView` is the load-bearing security boundary for
   SEC-001 / ADR-005. It has no `correct_answer` field, `extra="forbid"`
   rejects accidental widening, and it is constructed explicitly field-by-field
   from a deliberately allowlisted projection (see `question_search.py`).
   `AnswerKey` is a frozen dataclass with no JSON serializer — never the
   Pydantic / `model_dump` family — so a logging mistake on the server-only
   path cannot leak the key through `__str__` or `__repr__` formatting that
   pretty-prints `correct`.

Naming follows docs/coding-standards.md §1.2: domain primitives that are easy
to confuse are NewTypes (`QuestionId`, `LogicalId`, `LanguageCode`,
`OptionKey`); tool I/O is snake_case per 008-api §0.4.
"""

import dataclasses
from datetime import datetime
from enum import Enum
from typing import Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

QuestionId = NewType("QuestionId", str)
LogicalId = NewType("LogicalId", str)
LanguageCode = NewType("LanguageCode", str)
OptionKey = NewType("OptionKey", str)

# ISO 639-1 allowlist for v1. The full allowlist also lives in App
# Configuration (SEC-010) — this module-level constant is the build-time view.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "fr", "es"})

Difficulty = Literal["easy", "medium", "hard"]

# Bump on every breaking field-shape change to the Cosmos schema.
# See 008-api §6.5 (Document Schema Versioning).
COSMOS_SCHEMA_VERSION: int = 1


class Option(BaseModel):
    """A single multiple-choice option. Key is uppercase A–Z; text is human-readable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=1, pattern=r"^[A-Z]$")
    text: str = Field(min_length=1, max_length=512)


class Question(BaseModel):
    """Source-of-truth shape for an authored question record.

    One record per `(logical_id, language)` pair. Validates the JSON-on-disk
    shape; the seed loader (TASK-026) rejects records that fail this model.

    `correct_answer` is required at authoring time (it lives in AI Search under
    a server-only projection) but **never** crosses the LLM boundary — see
    `QuestionView` and `question_search.get_question_view`.
    """

    model_config = ConfigDict(extra="forbid")

    logical_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    topic: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    language: str = Field(min_length=2, max_length=2)
    text: str = Field(min_length=1, max_length=2000)
    options: list[Option] = Field(min_length=2, max_length=8)
    correct_answer: list[str] = Field(min_length=1, max_length=8)
    difficulty: Difficulty
    tags: list[str] = Field(default_factory=list, max_length=16)
    category: str = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=4000)
    score_weight: float = Field(default=1.0, ge=0.0, le=10.0)

    @field_validator("language")
    @classmethod
    def _language_in_allowlist(cls, value: str) -> str:
        # SEC-010 — fail at seed time, not at runtime.
        if value not in SUPPORTED_LANGUAGES:
            raise ValueError(f"language {value!r} not in allowlist {sorted(SUPPORTED_LANGUAGES)}")
        return value

    @field_validator("correct_answer")
    @classmethod
    def _correct_answer_uppercase(cls, value: list[str]) -> list[str]:
        for key in value:
            if not key or len(key) != 1 or not key.isupper() or not key.isalpha():
                raise ValueError(f"correct_answer entry {key!r} must be a single A–Z letter")
        return value

    @model_validator(mode="after")
    def _correct_answer_in_options(self) -> "Question":
        option_keys = {opt.key for opt in self.options}
        unknown = set(self.correct_answer) - option_keys
        if unknown:
            raise ValueError(
                f"correct_answer keys {sorted(unknown)} not in options {sorted(option_keys)}"
            )
        return self

    def index_id(self) -> str:
        """Build the AI Search document key (`<logical_id>-<language>` — NFR-011)."""
        return f"{self.logical_id}-{self.language}"


class QuestionView(BaseModel):
    """LLM-safe projection of an AI Search question record.

    This is the SEC-001 boundary. Every field is 🟢 (LLM-OK) per 008-api §0.1.
    `extra="forbid"` rejects accidental widening — if a future contributor
    expands the `selected_fields` allowlist in `question_search.get_question_view`
    without updating this model, construction fails loudly.

    **Do not** add `correct_answer`, `explanation`, `tags`, `category`, or
    `score_weight` to this model. They are server-side concerns (see
    `AnswerKey` for the answer-key channel; explanation rendering is governed
    by GOV-031 and lives in the tool layer, not the data layer).

    Field set mirrors 008-api §1.5.4 exactly.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    text: str
    options: list[Option]
    difficulty: Difficulty


@dataclasses.dataclass(frozen=True, slots=True)
class AnswerKey:
    """Server-only answer key. **Never** crosses the LLM boundary (SEC-002).

    Deliberately a plain frozen dataclass rather than a Pydantic model: the
    Pydantic surface includes `model_dump`, `model_dump_json`, and a stable
    `__repr__` formatting `correct` — every one of which is a potential leak
    vector. A `@dataclass` has only `__repr__` (which we override to mask) and
    no JSON serializer at all.

    `tasks/007 TASK-125` (AST lint) blocks references to `get_answer_key`
    outside the `submit_answer` function body. `tasks/005 TASK-088`'s
    defensive strip is the third layer.
    """

    question_id: str
    correct: frozenset[str]
    score_weight: float

    def __repr__(self) -> str:  # pragma: no cover - simple guard
        # Mask the correct set even in tracebacks; if this object ever leaks
        # into a log line, only its identity is visible.
        return f"AnswerKey(question_id={self.question_id!r}, correct=<redacted>)"

    def __str__(self) -> str:  # pragma: no cover
        return self.__repr__()


@dataclasses.dataclass(frozen=True, slots=True)
class FacetCount:
    """A single (topic, language) facet count from AI Search."""

    topic: str
    language: str
    count: int


# ---------------------------------------------------------------------------
# Session state machine (008-api §4.3)
# ---------------------------------------------------------------------------


class SessionStatus(str, Enum):
    ACTIVE = "Active"
    PAUSED = "Paused"
    EXPIRED = "Expired"
    COMPLETED = "Completed"
    SCORED = "Scored"


class Verdict(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PARTIAL = "partial"
    UNANSWERED = "unanswered"


class Channel(str, Enum):
    TEXT = "text"
    VOICE = "voice"


# ---------------------------------------------------------------------------
# Cosmos-bound base
# ---------------------------------------------------------------------------


class CosmosBase(BaseModel):
    """Base for documents serialised to/from Cosmos (camelCase wire).

    `populate_by_name=True` lets us construct with snake_case kwargs in code
    while round-tripping camelCase on the wire (008-api §0.4). `extra='ignore'`
    is required because Cosmos always returns `_rid`, `_self`, `_attachments`,
    and similar system fields that we deliberately don't model.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="ignore",
        use_enum_values=True,
    )


# ---------------------------------------------------------------------------
# Tool-I/O models (snake_case wire — 008-api §0.4)
# ---------------------------------------------------------------------------


class BreakdownItem(BaseModel):
    """One row of the results breakdown (008-api §1.7).

    `expected_answer` and per-option correctness are intentionally absent —
    SEC-001 boundary on the results envelope.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str
    verdict: Verdict
    score: float


class ResultsSummary(BaseModel):
    """Final results envelope returned by `get_results` and `submit_answer.results`.

    `pass` is a Python keyword; field aliased explicitly so the wire JSON
    keeps the snake_case name from 008-api §1.7.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", use_enum_values=True)

    session_id: str
    status: SessionStatus
    score: float
    max_score: float
    percentage: float
    is_pass: bool = Field(serialization_alias="pass", validation_alias="pass")
    pass_threshold_pct: float
    language: str
    duration_seconds: int
    breakdown: list[BreakdownItem]


# ---------------------------------------------------------------------------
# Cosmos documents (008-api §2)
# ---------------------------------------------------------------------------


class Answer(BaseModel):
    """One graded answer embedded in `SessionDoc.answers[]`.

    The example in 008-api §2.1 keeps snake_case keys inside the embedded
    list (`question_id`, `received_raw`, …) — so no `to_camel` here.
    `question_id` is the natural key the conditional-write idempotency
    check pivots on (TASK-047).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore", use_enum_values=True)

    question_id: str
    received_raw: str
    received_normalized: str | list[str] | None = None
    verdict: Verdict
    score_delta: float
    answered_at: datetime
    channel: Channel
    latency_ms: int


class SessionDoc(CosmosBase):
    """Authoritative session row (008-api §2.1)."""

    id: str
    user_id: str
    topic: str
    language: str
    requested_language: str
    seed: str
    shuffled_ids: list[str]
    current_index: int = 0
    answers: list[Answer] = Field(default_factory=list)
    score: float = 0.0
    max_score: float
    status: SessionStatus
    started_at: datetime
    question_started_at: datetime
    time_limit_seconds: int
    per_question_limit_seconds: int = 60
    pass_threshold_pct: float = 60.0
    channel: Channel
    schema_version: int = COSMOS_SCHEMA_VERSION

    # GOV-001..003. SHA-256 hex of the composed system prompt pinned at
    # `start_quiz`. Re-verified on every subsequent tool invocation
    # (TASK-070 / TASK-071). Mismatch is a P0 halt path. Nullable for
    # sessions created before task pack 004 (TASK-045 schema baseline).
    prompt_hash: str | None = None

    # Foundry `AgentThread` ID for ephemeral conversational state
    # (TASK-066). The thread is rehydratable but never authoritative;
    # durable state lives in this row per ADR-003.
    thread_id: str | None = None

    # Cosmos system fields. `_etag` is 🔴 SECRET-tier per 008-api §0.1 —
    # never logged, never surfaced to telemetry. Populated on every read;
    # required on every conditional write.
    etag: str | None = Field(default=None, alias="_etag")
    ts: int | None = Field(default=None, alias="_ts")
    ttl: int | None = None


class UserDoc(CosmosBase):
    """Per-user preferences row (008-api §2.2)."""

    id: str
    user_id: str
    language: str
    detected_language: str | None = None
    explicitly_set: bool = False
    created_at: datetime
    updated_at: datetime
    schema_version: int = COSMOS_SCHEMA_VERSION
    etag: str | None = Field(default=None, alias="_etag")


class TopicDoc(CosmosBase):
    """Topic catalog row (008-api §2.3).

    A topic IS a quiz definition: it parameterises the agent's tool calls
    (topic_id, default_language) and now carries a `default_n` so the
    operator can pre-configure how many questions a quiz on this topic
    runs by default. `start_quiz` falls back to this value when the user
    doesn't ask for an explicit count, then to `START_QUIZ_DEFAULT_N`
    (constant in tools.py) when the topic itself doesn't have one.
    """

    id: str
    topic_id: str
    labels: dict[str, str]
    counts: dict[str, int]
    default_language: str
    default_n: int | None = Field(default=None, ge=1, le=50)
    enabled: bool = True
    updated_at: datetime
    schema_version: int = COSMOS_SCHEMA_VERSION
    etag: str | None = Field(default=None, alias="_etag")


class AuditEvent(CosmosBase):
    """One grading-correctness event for dispute resolution (008-api §2.4).

    `expected` is SERVER-tier (🟡). It is permitted in this container
    because `audit` is RBAC-restricted and never readable from any tool
    surface that crosses the LLM boundary. The App Insights `grading_event`
    sink omits both `expected` and `received_raw` (008-api §4.5).
    """

    id: str
    session_id: str
    user_id: str
    question_id: str
    language: str
    channel: Channel
    expected: list[str]
    received: str
    received_raw: str
    verdict: Verdict
    score_delta: float
    latency_ms: int
    timestamp: datetime
    schema_version: int = COSMOS_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Tool I/O — wire shapes (snake_case per 008-api §0.4)
#
# Every response model below uses `extra="forbid"` so an accidental widening
# of the projection (or a tainted record reaching the model boundary) fails
# at validation time rather than silently broadening the SEC-001 surface.
# These are the **public** tool envelopes — they never carry `correct_answer`
# in any form, and the recursive defensive strip (TASK-088) is the third
# line of defence behind this typed boundary.
# ---------------------------------------------------------------------------


class _ToolModel(BaseModel):
    """Base for tool-I/O models — snake_case wire, strict field set."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# ---- list_topics (008-api §1.3) -------------------------------------------


class ListTopicsRequest(_ToolModel):
    language: str = Field(
        min_length=2,
        max_length=2,
        description=(
            "ISO 639-1 code (en / fr / es). Detect from the language of the "
            "user's most recent message and pass that; only override when the "
            "user explicitly requests a different language. Do NOT ask the user "
            "to choose a language unprompted."
        ),
    )
    user_id: str | None = None


class TopicSummary(_ToolModel):
    """One row in `ListTopicsResponse.topics` (008-api §1.3)."""

    topic_id: str
    label: str
    count: int = Field(ge=0)
    has_fallback: bool = False


class ListTopicsResponse(_ToolModel):
    language: str
    topics: list[TopicSummary]


# ---- set_language (008-api §1.4) ------------------------------------------


class SetLanguageRequest(_ToolModel):
    user_id: str
    language: str = Field(
        min_length=2,
        max_length=2,
        description=(
            "ISO 639-1 code (en / fr / es). Only call `set_language` when the "
            "user EXPLICITLY asks to change their preferred language (e.g. "
            "'switch to French', 'use Spanish from now on'). Do NOT call this "
            "tool just because the user happened to write in a different "
            "language — that's handled by passing `language` on each tool call."
        ),
    )


class SetLanguageResponse(_ToolModel):
    user_id: str
    language: str
    updated_at: datetime


# ---- start_quiz (008-api §1.5) --------------------------------------------


class StartQuizRequest(_ToolModel):
    user_id: str
    topic: str = Field(min_length=1, max_length=64)
    n: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of questions (1..50). OPTIONAL — leave unset to use the "
            "topic's pre-configured `default_n` (typically 10). Do NOT ask the "
            "user 'how many questions?' unprompted; only pass an explicit value "
            "when the user volunteers a number in their message."
        ),
    )
    language: str = Field(
        min_length=2,
        max_length=2,
        description=(
            "ISO 639-1 code (en / fr / es). Detect from the language of the "
            "user's most recent message and pass that; only override when the "
            "user explicitly requests a different language. Do NOT ask the user "
            "to choose a language unprompted."
        ),
    )
    difficulty: Literal["easy", "medium", "hard", "mixed"] = Field(
        default="mixed",
        description=(
            "Quiz difficulty. Defaults to 'mixed' — use that whenever the user "
            "doesn't explicitly state a preference. Do NOT ask the user about "
            "difficulty unprompted; only set this when they explicitly request "
            "'easy', 'medium', or 'hard'."
        ),
    )
    time_limit_seconds: int = Field(default=600, ge=60, le=3600)
    channel: Channel = Channel.TEXT


class FallbackNotice(_ToolModel):
    """Present on `StartQuizResponse` iff resolved language ≠ requested
    OR a count clamp was applied.

    Per the consent-flow contract (GOV-025 / TASK-189), the tool layer
    **never** silently switches languages — `language` == `requested` on
    every success path; this notice is the COUNT-CLAMP signal (`reason`
    set to `"count_clamped"`). Language switches only occur via an
    agent-mediated `set_language` + re-call.
    """

    requested: str
    resolved: str
    reason: Literal["count_clamped", "no_coverage_in_requested_language"]
    requested_n: int | None = None
    resolved_n: int | None = None


class StartQuizResponse(_ToolModel):
    session_id: str
    question: QuestionView
    index: int = Field(ge=1)
    total: int = Field(ge=1)
    language: str
    fallback_notice: FallbackNotice | None = None
    time_limit_seconds: int
    question_started_at: datetime


# ---- submit_answer (008-api §1.6) -----------------------------------------


class SubmitAnswerRequest(_ToolModel):
    session_id: str
    question_id: str
    raw_answer: str = Field(min_length=1, max_length=512)
    channel: Channel = Channel.TEXT
    client_timestamp: datetime | None = None


class SubmitAnswerResponse(_ToolModel):
    verdict: Verdict
    score_delta: float
    running_score: float
    index: int = Field(ge=1)
    total: int = Field(ge=1)
    next: QuestionView | None = None
    explanation: str | None = None
    done: bool
    results: ResultsSummary | None = None
    question_started_at: datetime | None = None


# ---- get_results (008-api §1.7) -------------------------------------------


class GetResultsRequest(_ToolModel):
    session_id: str
    user_id: str


# `GetResultsResponse` is the existing `ResultsSummary` (already
# snake_case + `extra="forbid"`, with the `pass` keyword alias handled).
GetResultsResponse = ResultsSummary


# ---- Error envelope (008-api §4.2) ----------------------------------------


class ToolErrorDetail(BaseModel):
    """Free-form detail carrier. Always 🟡 — server-only.

    Renderer.render_error (008-api §6.4) drops this field before the
    string reaches LLM context; it survives only in App Insights via
    customDimensions keyed by `correlation_id`.
    """

    model_config = ConfigDict(extra="allow")


class ToolError(_ToolModel):
    code: str
    message_user_key: str
    message_user: str | None = None
    correlation_id: str | None = None
    retryable: bool = False
    retry_after_ms: int | None = None
    detail: dict[str, object] | None = None


class ToolEnvelope(BaseModel):
    """Discriminated union envelope returned by every tool (008-api §0.3).

    Tools construct this via the `ok=True/False` flag; the dispatcher's
    `ToolResult` is the in-process shape, and the JSON serialisation is
    this envelope. Never widens — `extra="forbid"`.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    ok: bool
    data: dict[str, object] | None = None
    error: ToolError | None = None


__all__ = [
    "COSMOS_SCHEMA_VERSION",
    "SUPPORTED_LANGUAGES",
    "Answer",
    "AnswerKey",
    "AuditEvent",
    "BreakdownItem",
    "Channel",
    "CosmosBase",
    "Difficulty",
    "FacetCount",
    "FallbackNotice",
    "GetResultsRequest",
    "GetResultsResponse",
    "LanguageCode",
    "ListTopicsRequest",
    "ListTopicsResponse",
    "LogicalId",
    "Option",
    "OptionKey",
    "Question",
    "QuestionId",
    "QuestionView",
    "ResultsSummary",
    "SessionDoc",
    "SessionStatus",
    "SetLanguageRequest",
    "SetLanguageResponse",
    "StartQuizRequest",
    "StartQuizResponse",
    "SubmitAnswerRequest",
    "SubmitAnswerResponse",
    "ToolEnvelope",
    "ToolError",
    "TopicDoc",
    "TopicSummary",
    "UserDoc",
    "Verdict",
]
