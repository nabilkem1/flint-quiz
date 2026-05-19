# Engineering Coding Standards

- **Version**: v1.0
- **Last reviewed**: 2026-05-17
- **Owner**: Platform Engineering + Security
- **Status**: Accepted — **mandatory** for all code merged to `main`
- **Audience**: every engineer (human or AI-assisted) writing or reviewing code in this repository

---

## 0. Charter

This document defines the **mandatory engineering standards** for the Flint Quiz platform — a multilingual conversational quiz system built on Microsoft Agent Framework (MAF), Azure AI Foundry, Cosmos DB, Azure AI Search, the Azure Realtime API, and Python.

These standards are **enforceable**, not aspirational. Where a rule is stated as MUST or MUST NOT, it is a merge-blocking requirement enforced by CI, linters, tests, or code review. SHOULD/SHOULD NOT are strong defaults that require justification (recorded in PR description) to deviate from.

This document is **subordinate to**, and consistent with:

- [`specs/002-system-architecture.md`](../specs/002-system-architecture.md) — what we build
- [`specs/005-security-model.md`](../specs/005-security-model.md) — what we must not let happen
- [`specs/008-api-contracts.md`](../specs/008-api-contracts.md) — wire-level contracts
- [`specs/009-agent-governance.md`](../specs/009-agent-governance.md) — behavioral contracts
- [`adr/*`](../adr/) — architectural decisions

Where this document conflicts with the specs above, **the specs win**. Open a PR to reconcile this document.

### 0.1 Document Precedence

When this document conflicts with another, this is the resolution order — highest authority first:

1. **ADRs (`adr/*`)** — architectural decisions, immutable until superseded by a follow-on ADR.
2. **Specs (`specs/00*`)** — `008-api-contracts.md` is authoritative for wire-level; `009-agent-governance.md` is authoritative for agent behavior; `005-security-model.md` is authoritative for security requirements. Where two specs disagree, the most-recent / highest-numbered owning that subject wins (per their "supersedes" notes).
3. **This document (`docs/coding-standards.md`)** — authoritative for Python conventions, repo layout, dependency injection, exception hierarchy names, lint/format configuration.
4. **`docs/ai-agent-development-guidelines.md`** — authoritative for agent-loop philosophy, AI-engineering policy, model-upgrade process, anti-patterns.

Where (3) and (4) overlap (e.g., telemetry conventions, error envelope, idempotency, security boundary, multilingual rules), **this document wins for Python/repo concerns; the agent-development-guidelines wins for AI-loop concerns**. When neither is clearly applicable, open a PR against both to reconcile.

When **any** of (3) or (4) conflicts with (1) or (2), (1) and (2) win and a PR is opened to fix (3) or (4).

### 0.2 Severity Tiers (used throughout)

| Tier | Marker | Meaning |
|------|--------|---------|
| P0   | 🔴 | Security or scoring-integrity violation. CI must block; merge prohibited. |
| P1   | 🟠 | Correctness/behavioral contract violation. CI should block; lead reviewer required. |
| P2   | 🟡 | Quality regression. Lint-warn; reviewer judgement. |

### 0.3 Field-Level Sensitivity Tiers (mirrors [`008-api §0.1`](../specs/008-api-contracts.md))

| Tier | Marker | Definition |
|------|--------|------------|
| `LLM-OK` | 🟢 | May appear in tool returns that pass through the agent's LLM context. |
| `SERVER` | 🟡 | Server-only. **Never** in any string returned to the agent or to App Insights. |
| `SECRET` | 🔴 | Sensitive material (credentials, etag tokens used for auth). Never logged in cleartext. |

Any code that allows a 🟡 or 🔴 field to cross into LLM context, log output, or untrusted telemetry is a **P0 defect**.

---

## 1. Python Standards

### 1.1 Language Version

- Python **3.12+** only. No support targets below 3.12 in v1.
- Use `from __future__ import annotations` is **NOT required** (we are on 3.12, PEP 563 default is fine without it; do not mix styles).

### 1.2 Typing — Mandatory

- **All** function signatures, methods, and module-level constants MUST have type annotations.
- Public-API parameters and return types MUST use specific types — no bare `Any`, no bare `dict`/`list`. Use `dict[str, X]`, `list[X]`, `Sequence[X]`, `Mapping[str, X]`, etc.
- Use `Literal[...]`, `TypedDict`, `Protocol`, `NewType`, and `Annotated` where they make a contract clearer than a comment.
- Prefer **Pydantic models** over `TypedDict` for any value that crosses a boundary (tool input/output, Cosmos document, HTTP request/response). `TypedDict` is acceptable for purely internal, hot-path-sensitive structures where Pydantic overhead is measurable.
- Domain primitives that are easily confused MUST be `NewType`s: `UserId = NewType("UserId", UUID)`, `SessionId = NewType("SessionId", UUID)`, `LogicalId = NewType("LogicalId", str)`, `QuestionId = NewType("QuestionId", str)`, `OptionKey = NewType("OptionKey", str)`, `LanguageCode = NewType("LanguageCode", str)`.

#### GOOD

```python
from typing import Sequence
from uuid import UUID

from src.data.models import Answer, SessionDoc, UserId, SessionId, QuestionId

async def grade_answer(
    session_id: SessionId,
    question_id: QuestionId,
    received_normalized: str,
    expected: Sequence[str],
) -> Answer:
    ...
```

#### BAD

```python
async def grade_answer(session_id, question_id, received, expected):  # untyped
    ...

def get_session(id) -> dict:  # bare dict, ambiguous id
    ...
```

### 1.3 `mypy` Enforcement

- `mypy --strict` MUST pass on `src/`. CI fails on any new `# type: ignore` without an inline justification comment of the form `# type: ignore[error-code]  # <reason>`.
- Third-party libraries without stubs MUST be wrapped in a thin typed adapter under `src/data/` or `src/agent/`; raw imports of untyped libraries from business logic is **forbidden**.
- `Any` may appear only at the boundary of an untyped third-party call, and SHOULD be erased within one function.
- No `from typing import *`. No `cast(Any, x)` as a silencer.

### 1.4 Formatting — Ruff + Black

- **Ruff** is the source of truth for lint. **Black** is the source of truth for formatting. Both run in pre-commit and CI.
- Line length: **100** characters. (Long URLs and SQL/KQL strings may exceed with `# noqa: E501` and a justification.)
- Ruff rule sets enabled (non-exhaustive — see `pyproject.toml`): `E`, `F`, `W`, `B` (bugbear), `I` (isort), `UP` (pyupgrade), `S` (bandit), `ASYNC`, `RET`, `SIM`, `PL`, `RUF`, `TRY`, `ERA`, `PT` (pytest), `LOG`. Removing a rule requires a PR with a security review.
- Black profile: default. Ruff `isort` profile: `black`.
- No reformatting commits mixed with logic changes — formatting goes in its own PR.

### 1.5 Import Organization

Five groups, separated by a blank line, in this fixed order:

1. `__future__` imports
2. Standard library
3. Third-party (`azure-*`, `pydantic`, `pytest`, ...)
4. First-party (`src.*`)
5. Local (`from . import ...`)

- No wildcard imports (`from x import *`). Forbidden.
- No conditional imports inside functions, except to break true circular dependencies (which must instead be refactored).
- Within a module, sort imports alphabetically (Ruff `I` enforces this).
- Re-exports must be explicit: an `__init__.py` that re-exports MUST declare `__all__`.

#### GOOD

```python
import asyncio
import logging
from typing import Sequence
from uuid import UUID

from azure.cosmos.aio import ContainerProxy
from pydantic import BaseModel

from src.data.models import SessionDoc
```

### 1.6 Docstring Conventions

- **Google-style** docstrings. Enforced by Ruff `D` rule subset on `src/`.
- Every public module, class, function, and method has a docstring. Private helpers may omit the docstring only if the function name and signature are self-explanatory.
- Docstrings explain **why** and document **invariants, side effects, and contracts** — not what the code already says.
- For tools, the docstring is the **agent-facing description** (MAF reads it). It MUST:
  - Begin with a one-sentence purpose suitable for LLM consumption.
  - Document each argument with type, semantics, and validation rules.
  - State the security tier of every returned field (🟢/🟡/🔴) — see [`008-api §0.1`](../specs/008-api-contracts.md).
  - State the idempotency class (`R`/`I-U`/`I-K`/`I-S`) — see [`008-api §1.2`](../specs/008-api-contracts.md).
  - Cross-reference the relevant `SEC-*`, `GOV-*`, or `NFR-*` IDs.

#### GOOD — tool docstring

```python
async def submit_answer(
    session_id: SessionId,
    question_id: QuestionId,
    answer: str,
) -> SubmitAnswerResult:
    """Grade a single answer deterministically and persist the result.

    Reads the answer key server-side (never returned to caller), grades with a
    deterministic set comparison, then performs a Cosmos conditional write
    keyed on (session_id, question_id) for idempotency.

    Args:
        session_id: The active session UUID. Must be in state `Active`.
        question_id: The question being answered, matching the next
            unanswered position in the session's shuffled list.
        answer: The user's raw answer string. Will be normalized to an
            option key by `answer_normalizer` before grading.

    Returns:
        SubmitAnswerResult with 🟢 fields only: verdict, score_delta,
        next_question (no answer key), or final results on completion.

    Idempotency:
        Class `I-K`. Duplicate calls for the same (session_id, question_id)
        return the cached verdict; the score increments at most once.
        Enforced by Cosmos `ifMatch` etag — see SEC-006, NFR-002.

    Security:
        SEC-001/SEC-002 — `correct_answer` is never serialized to the caller.
        The grader runs in Python; the LLM is not in the grading path.
    """
```

### 1.7 Async / Await Rules

- All I/O — Cosmos, AI Search, Key Vault, HTTP — MUST be `async`. There are **no** synchronous code paths into Azure SDKs in `src/`.
- Use `azure.cosmos.aio`, `azure.search.documents.aio`, `azure.keyvault.secrets.aio`.
- Never call `asyncio.run` inside a library function — only at the process entrypoint (`src/agent/quiz_agent.py` boot, scripts under `src/seed/`, tests).
- **Never** `time.sleep` in async code. Use `await asyncio.sleep(...)`.
- **Never** make blocking calls (file I/O, requests, sync SDK) inside an `async def` without `asyncio.to_thread`. Lint rule `ASYNC100` enforces this; bypassing it requires a P1 review.
- Bound concurrent fan-out with `asyncio.Semaphore` or `asyncio.TaskGroup` (3.11+). Unbounded `asyncio.gather` over user-driven input is **forbidden**.
- Cancellation: every `async def` MUST be cancellation-safe — clean up resources via `try/finally` or `async with`. Catching `asyncio.CancelledError` to suppress it is **forbidden** unless re-raised.

#### GOOD

```python
async def fetch_questions(ids: Sequence[QuestionId]) -> list[QuestionView]:
    semaphore = asyncio.Semaphore(8)

    async def _one(qid: QuestionId) -> QuestionView:
        async with semaphore:
            return await question_search.get_question_view(qid)

    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(_one(qid)) for qid in ids]
    return [t.result() for t in tasks]
```

#### BAD

```python
async def fetch_questions(ids):
    # Unbounded fan-out — will hammer Search on a long list.
    return await asyncio.gather(*(question_search.get(q) for q in ids))

def get_session(id):  # sync path into async SDK
    return asyncio.run(_cosmos.read_item(id, id))
```

### 1.8 Error Handling

- **Never** catch bare `Exception` except at process boundaries (the agent dispatcher, the seed CLI). Catch the **narrowest** exception that can actually occur.
- **Never** catch and silently `pass`. Every caught exception MUST be either re-raised, converted to a domain exception, or logged at WARNING+ with structured context.
- Validation failures at API/tool boundaries become domain exceptions (see §1.9). Internal bugs propagate as `AssertionError` or programmer-error subclasses — do not swallow.
- Retries live in **one place**: the SDK retry policy (Cosmos/Search clients) plus an explicit `tenacity` decorator on idempotent operations. Ad-hoc `for _ in range(3): try/except` blocks are **forbidden**.

### 1.9 Exception Hierarchy

Defined once in `src/common/exceptions.py`. **No** module may declare its own exception base outside this file.

```python
class FlintError(Exception):
    """Base for all domain exceptions."""

class FlintValidationError(FlintError):
    """User-correctable input was invalid (HTTP 400 equivalent)."""

class InvalidLanguageError(FlintValidationError): ...

class FlintAuthorizationError(FlintError):
    """Caller lacks the required claim/role (HTTP 403 equivalent)."""

class FlintNotFoundError(FlintError):
    """A referenced resource does not exist (HTTP 404 equivalent)."""

class SessionStateError(FlintError):
    """Attempted state transition forbidden by 008-api §4.3."""

class FlintConflictError(FlintError):
    """Conditional write lost the race (Cosmos 412)."""

class FlintUpstreamError(FlintError):
    """A downstream Azure service failed (Cosmos 5xx, Search 5xx)."""

class FlintConfigurationError(FlintError):
    """Misconfiguration — fail loud on startup, never at request time."""

class AnswerLeakageError(FlintError):
    """🔴 P0 — a 🟡 field was about to cross the LLM boundary."""
```

- Tool functions MUST translate these to the error envelope from [`008-api §0.3`](../specs/008-api-contracts.md): `{"ok": False, "error": {...}}`. Internal codes and stack traces never reach the LLM.
- `AnswerLeakageError` is **always** P0: log + alert + halt session (see [`009-gov §0.2`](../specs/009-agent-governance.md)).

### 1.10 Logging Standards

- Library: stdlib `logging` configured by `src/common/logging_setup.py`. **No** `print`. No `logger = logging.getLogger("flint")` — always `__name__`.
- All logs are **structured**. Use `logger.info("event", extra={...})` with a stable event name as the message. No f-strings in log messages for variable data — put data in `extra`.
- Mandatory dimensions on every log line (added by a `LoggerAdapter` / OTel processor): `service`, `env`, `correlation_id`, `session_id` (when in scope), `user_id` (opaque OID only), `language`, `channel`.
- **Forbidden** in any log line: `correct_answer`, `answer_key`, connection strings, Cosmos `_etag` values, any Key Vault secret material, full `ErrorEnvelope` interpolation (use `correlation_id` instead — see §6.5). CI lint `tests/test_log_redaction.py` enforces.
- **Permitted at INFO with retention discipline (SEC-008)**: `raw_answer` up to the spec 008 §4.1 length cap (512 chars). The same value flows to `audit.receivedRaw` (server-only, RBAC-restricted) — the two surfaces must carry the **same** value so dispute triage can join them. PII redaction is enforced by App Insights retention policy (default 30 days for transcript-bearing customEvents per ADR 006), **not** by per-log-line truncation/hashing — hashing breaks the dispute-resolution chain.
- **Etag policy**: Cosmos `_etag` values are 🔴 by [`008-api §0.1`](../specs/008-api-contracts.md). They MUST NOT appear in any log line, span attribute, or telemetry custom event — not even structured fields. Span lint (TASK-144) blocks `_etag` as an attribute name; log lint blocks it as a structured-field key.
- Log levels:
  - `DEBUG` — developer-only detail; never in prod by default.
  - `INFO` — domain events (tool started, tool succeeded, session created).
  - `WARNING` — recoverable but unexpected (retried operation, fallback engaged).
  - `ERROR` — request-scoped failure surfaced to user.
  - `CRITICAL` — process-wide failure or P0 governance violation.

#### GOOD

```python
logger.info(
    "submit_answer.persisted",
    extra={
        "session_id": str(session_id),
        "question_id": question_id,
        "verdict": verdict.value,
        "latency_ms": latency_ms,
        "channel": channel.value,
    },
)
```

#### BAD

```python
logger.info(f"Persisted answer {answer} for {session_id}, correct was {correct_answer}")
# - Unstructured.
# - Leaks the answer key. P0.
```

### 1.11 Dependency Injection

- **Constructor injection.** No module-level singletons that bind to live SDK clients. Test seams matter more than terseness.
- A small composition root in `src/agent/composition.py` constructs concrete dependencies once at startup and wires them into the agent and tools. Tools accept their dependencies as keyword args with `Protocol`-typed defaults — no `get_global_cosmos()` style accessors.
- The DI graph MUST be acyclic. Circular imports are a refactor signal, not a problem to suppress.

#### GOOD

```python
class SubmitAnswerTool:
    def __init__(
        self,
        *,
        sessions: SessionsRepository,
        search: QuestionSearch,
        normalizer: AnswerNormalizer,
        clock: Clock,
        telemetry: Telemetry,
    ) -> None:
        self._sessions = sessions
        self._search = search
        self._normalizer = normalizer
        self._clock = clock
        self._telemetry = telemetry
```

### 1.12 Time-Zone and Clock Discipline

- **All timestamps are UTC**, ISO 8601 with `Z` suffix per [`008-api §0.2`](../specs/008-api-contracts.md). No local-time values are persisted or transmitted, ever. Pydantic models use `datetime` with `tzinfo=datetime.UTC` enforced via a validator.
- **Wall-clock vs monotonic clock**:
  - For persisted timestamps (`startedAt`, `questionStartedAt`, `answered_at`, `timestamp`): wall-clock UTC from the injected `Clock.now() -> datetime` (see §1.11). Backed by `datetime.now(tz=datetime.UTC)` in production.
  - For latency measurement (`latency_ms`, span durations): monotonic clock via `time.monotonic_ns()` or the OTel span duration. Never compute latency from two wall-clock samples — clock skew, leap seconds, and NTP adjustments make wall-clock differences unreliable on the sub-second scale of NFR-001.
- **Cross-region**: all services pin to UTC. No deployment-time region-local clocks. Region failover does not require timestamp translation.
- **Test discipline**: tests inject `FrozenClock(at=datetime(...))` with explicit `tz=datetime.UTC`. A test without `tz` is rejected by the `Clock` constructor.

### 1.13 Configuration Management

- **All** configuration is read **at startup** by `src/common/config.py` from:
  1. Environment variables (set by the Hosted Agent's MI-bound App Configuration reference).
  2. App Configuration (for runtime-tunable values: `languages:supported`, `features:*`, latency budgets).
  3. Key Vault via MI (for secrets only — see SEC-013 / TASK-122).
- The result is a **frozen Pydantic `BaseSettings` instance** passed through DI. Reading `os.environ` outside `config.py` is **forbidden**.
- Defaults live in code; per-env overrides in App Configuration. Never check secrets, endpoints, or connection strings into the repo (see §7).
- Config schema changes are a versioned migration: bump `Config.schema_version`, document in `docs/secrets.md` and `docs/llm-boundary.md` if relevant.

#### BAD

```python
COSMOS_ENDPOINT = os.environ["COSMOS_ENDPOINT"]  # at module top — fails at import on misconfig, not at startup
```

---

## 2. Architecture Standards

### 2.1 Separation of Concerns — Layer Map

| Layer | Folder | Allowed dependencies | Forbidden |
|-------|--------|----------------------|-----------|
| **Domain models** | `src/data/models.py` | stdlib, pydantic | Azure SDKs, logging, telemetry |
| **Repositories** (data access) | `src/data/{cosmos_repository,question_search,keyvault_client,erasure}.py` | domain models, Azure SDKs, logging | tools, agent, prompts |
| **Tools** (deterministic business logic) | `src/agent/tools.py`, `src/agent/answer_normalizer.py`, `src/agent/tts_shaper.py` | repositories, domain models, common | agent shell, prompts, MAF runtime |
| **Agent shell** | `src/agent/quiz_agent.py`, `src/agent/composition.py`, `src/agent/prompts/` | tools, MAF, telemetry | repositories (must go through tools), domain logic |
| **Common** | `src/common/{config,exceptions,logging_setup,telemetry,clock}.py` | stdlib, OTel | everything domain-specific |
| **Entrypoints** | `src/seed/`, deploy scripts | all of the above | — |

Dependencies flow **downward** only. `import-linter` enforces this; CI fails on a violation.

### 2.2 Deterministic Business Logic

**Grading, normalization, shuffling, timer evaluation, and scoring MUST be pure deterministic Python.** No LLM call may participate in:

- Verdict computation (`submit_answer` → set comparison).
- Score computation.
- Answer normalization.
- Random question selection (use a seeded RNG per session — see [`002-arch §10`](../specs/002-system-architecture.md)).
- Timer enforcement (server clock + stored `questionStartedAt`).
- State transitions on `SessionDoc.status`.

The LLM is a **conversational shell** ([`004-agent §2`](../specs/004-agent-behavior.md)). If you find yourself asking the model "is this right?", **stop and write Python**.

### 2.3 Tool Boundaries

- Tools are the **only** way the agent causes side effects. The agent never reads Cosmos, never queries Search, never calls Key Vault directly.
- Each tool corresponds 1:1 to a contract in [`008-api §1`](../specs/008-api-contracts.md). The v1 set is fixed: `list_topics`, `set_language`, `start_quiz`, `submit_answer`, `get_results`. Adding a sixth tool requires an ADR and updates to specs 003, 004, 008, 009.
- Tools MUST validate inputs with Pydantic at the boundary and translate domain exceptions to the error envelope. **No** unvalidated dict reaches a repository.
- A tool MUST NOT call another tool. If two tools share logic, extract a function in `src/agent/_tool_helpers.py`; tools call helpers, never each other.
- Tools MUST be stateless across calls. Per-session state lives in Cosmos.

### 2.4 Repository Pattern

- Every Cosmos container and every Search index is fronted by a **repository class** in `src/data/`. There are no scattered `container.read_item(...)` calls.
- Repository methods take and return **domain models** (Pydantic), not raw dicts. The repository is responsible for the camelCase↔snake_case translation (see [`008-api §0.4`](../specs/008-api-contracts.md)).
- Repositories MUST expose narrow methods (`get_session`, `replace_session_if_match`, `query_active_sessions_for_user`) rather than a generic `query(sql)`. SQL strings live inside the repository; no SQL anywhere else.
- The `QuestionSearch` repository MUST expose **two separate methods** ([ADR-005](../adr/005-tool-boundary-prevents-answer-leakage.md)):
  - `get_question_view(question_id) -> QuestionView` — 🟢 fields only.
  - `get_answer_key(question_id) -> AnswerKey` — 🟡; can only be imported by `submit_answer` (AST-linted, TASK-125).

### 2.5 Service Layer Pattern

For logic that spans multiple repositories (e.g., the GDPR erasure cascade), a **service module** under `src/data/` (e.g., `erasure.py`) orchestrates the repositories. Service modules:

- Are **stateless**.
- Compose repositories via DI.
- Define their own request/response models in `src/data/models.py`.
- Emit telemetry; do not log secrets or 🟡 fields.

### 2.6 Idempotency Requirements

- Every write operation MUST declare its idempotency class (`R`, `I-U`, `I-K`, `I-S`) in code and in the contract doc.
- `I-K` operations (`submit_answer`) MUST use Cosmos `ifMatch` etag conditional writes with `(session_id, question_id)` as the natural key. Idempotency CANNOT be implemented with "check then write" — only with the SDK's conditional-write primitive.
- `I-U` operations (`set_language`) MUST be safe to retry without checking prior state.
- `I-S` operations (`start_quiz`) MUST detect an in-flight `Active` session for the same `(user_id, topic)` within `time_limit_seconds` and return `ok: false` with `code: E_SESSION_ACTIVE` and `active_session_id` in `detail`, per [`008-api §1.5.6`](../specs/008-api-contracts.md). The agent then surfaces the resume affordance in the active-language phrasing block. **Do NOT** transparently re-attach by returning success with the existing session — that bypasses the explicit resume conversation the spec requires.
- A retry/replay test exists for every write tool (`tests/test_idempotency.py`, TEST-007). New write tool ⇒ new test row.

### 2.7 Stateless Agent Behavior

- The MAF agent process MUST be horizontally scalable. No in-process state survives a request that isn't either (a) ephemeral within the request or (b) cached read-only data (topic catalog).
- **Forbidden** in-process state:
  - Per-session counters, locks, or queues. Use Cosmos.
  - Per-user preferences. Use Cosmos `users`.
  - Tool result caching keyed by user input. Stateless reads OK; computed-from-input caches require an ADR.
- The agent thread (Foundry-managed) is **ephemeral** — durable state lives in Cosmos. Re-deriving from the thread on resume is a bug ([`002-arch §7`](../specs/002-system-architecture.md)).

### 2.8 Domain Model Consistency

- The five canonical Pydantic models — `QuestionView`, `AnswerKey`, `SessionDoc`, `Answer`, `ResultsSummary`, `UserDoc`, `TopicDoc`, `AuditEvent` — are defined **once** in `src/data/models.py` and re-exported via `src/data/__init__.py`. Duplicate definitions in tests or scripts are **forbidden**.
- Field names match [`008-api`](../specs/008-api-contracts.md) exactly. Renaming a field is a breaking change requiring an ADR.
- `QuestionView` is an **allowlist projection** — its class definition has no `correct_answer` field at all, not even `Optional[None]`. This is structurally enforced, not defensively stripped. ([ADR-005](../adr/005-tool-boundary-prevents-answer-leakage.md))
- `AnswerKey` has no JSON serializer (`model_config = ConfigDict(arbitrary_types_allowed=False)`, explicit `model_dump_json` override that raises `AnswerLeakageError`).

---

## 3. AI / LLM Coding Standards

### 3.1 No Answer Leakage (🔴 P0)

The model context **MUST NEVER contain a `correct_answer` value**, in any language, in any layer (system prompt, user turn, tool result, telemetry trace attribute, error message). This is SEC-001 and the cornerstone of [ADR-005](../adr/005-tool-boundary-prevents-answer-leakage.md).

Enforcement (defense in depth):

1. **Structural** — `QuestionView` has no `correct_answer` field; `AnswerKey` has no serializer.
2. **Architectural** — `get_answer_key` is import-restricted to the body of `submit_answer` (AST lint, TASK-125).
3. **Runtime** — every tool return is passed through a recursive strip walk that fails on any key in `{"correct_answer", "answer_key", "answers", "expected"}` (TASK-124 / TASK-088). Failure raises `AnswerLeakageError` and emits P0.
4. **Test** — `tests/test_no_answer_leakage.py` (TEST-006) runs on every change to `src/agent/tools.py`, `src/data/question_search.py`, `src/agent/quiz_agent.py`.

### 3.2 No Grading Inside the LLM

The model MUST NOT be asked, in any prompt layer or tool result, to evaluate correctness. Forbidden patterns:

#### BAD

```python
# In a prompt:
"You are a quiz grader. The correct answer is B. Decide if 'la deuxième' matches."

# In code:
verdict = await llm.complete(
    f"Did the user answer correctly? Question: {q.text} "
    f"Correct: {key.correct_answer} User said: {answer}"
)
```

Grading is `set(received_normalized) == set(expected)` in Python. The verdict goes back to the LLM as a string; the comparison does not.

### 3.3 Tool-Only Deterministic Logic

Any behavior that affects scoring, state, or persisted data is a **tool**, not a prompt instruction. "When the user asks for results, return the score" is fine; "Compute the score as correct/total" is not.

### 3.4 Prompt Isolation (GOV-001, GOV-002)

- The composed system prompt has **four fixed layers** in fixed order ([`009-gov §1.1`](../specs/009-agent-governance.md)): Identity, Behavioral Contract, Per-Language Phrasing Block, Session Frame.
- **No tool output, no retrieved document, no user input is ever inlined into the system prompt.** User content lives only in user-role turns.
- Layer files live under `src/agent/prompts/`. Editing them is a versioned change (SHA-256 logged on session start; mid-session mismatch is P0 — GOV-003 / TASK-071).
- A prompt MUST NOT contain forbidden content (GOV-005): any `correct_answer` value, user PII beyond opaque OID, secrets/etags, conditional grading logic, real bank content as few-shot. `tests/test_prompt_redaction.py` (TEST-018) enforces.

### 3.5 Context Minimization

- The LLM context per turn carries: the composed system prompt (4 layers), the last N user/assistant turns the MAF thread retains, and the result of the most recent tool call. **Nothing else.**
- `remaining_question_ids[]` does NOT go into the prompt — it lives in Cosmos. Adding "context" to keep state in the prompt is **forbidden** ([`002-arch §7`](../specs/002-system-architecture.md)).
- Few-shot examples, if used at all, are **synthetic** and constant — never include real bank content.

### 3.6 Token Efficiency

- The per-language phrasing block selects **one** language at session start; never all three concatenated (GOV-004). Sending all three is a P2 quality regression.
- Tool result strings are **single-purpose**: deliver the next question, or deliver feedback + the next question, or deliver final results. Not all three in one blob.
- Avoid model "let me think out loud" preambles in prompts — they cost tokens and hurt voice latency (NFR-001).

### 3.7 Hallucination Prevention

- The agent NEVER invents:
  - Question text, options, or explanations not present in `QuestionView`. (Synthesized explanation = P1 — TEST-020 / GOV-031.)
  - Topic names, language codes, score values, or session IDs.
  - Tool argument values not derived from user input or prior tool output.
- The agent MUST refuse politely (in the active-language phrasing block — GOV-052) when asked for content outside its scope. Refusal copy is **not** improvised; it comes from the phrasing block (TEST-021).

### 3.8 Multilingual Consistency

- Language is **resolved once** per session and persisted on `SessionDoc.language`. Mid-session language switches go through `set_language` and require explicit consent for coverage gaps (GOV-024 / GOV-025 / TEST-022).
- The model responds in `SessionDoc.language`. Code-switching detected in a user utterance does NOT trigger an implicit language switch.
- All user-facing strings — greeting, framing, error/refusal — come from `prompts/lang/{en,fr,es}.yaml`. **No** user-facing English-by-default in an `fr` or `es` session (TEST-021).
- Tests are **parametrized over languages** (`@pytest.mark.parametrize("language", ["en", "fr", "es"])`). A test that runs only in English on a multilingual code path is incomplete.

### 3.9 Voice-Safe Formatting (GOV-050, NFR-014)

Tool return strings rendered to a voice channel MUST satisfy:

- No markdown characters (`*`, `_`, `` ` ``, `#`, `>`, `|`, `[`, `]`).
- No raw URLs. Spell domain on first mention; "the documentation page".
- Options rendered as `"Option A: ... Option B: ..."` — never as a bulleted list, never as letters-only.
- Numerals ≤ 100 spelled out ("ten questions", not "10 questions"). Above 100, prefer digit form.
- Acronyms expanded on first mention ("Virtual Private Network, V P N").
- Sentence-length blocks; no paragraphs over ~40 words.

Enforced by `tests/test_tts_invariants.py` (TEST-024) and the `tts_shaper` helper in `src/agent/tts_shaper.py`.

---

## 4. Cosmos DB Standards

### 4.1 Partition Strategy

| Container  | Partition key | Rationale |
|------------|---------------|-----------|
| `sessions` | `/userId`     | Hot writes per user; all session queries for resume are point reads or single-partition scans. ([`003-data §4.1`](../specs/003-data-contracts.md)) |
| `users`    | `/userId`     | Point read per request. |
| `topics`   | small catalog | Single logical partition is acceptable; document in repository. |
| `audit`    | `/sessionId`  | Aligns with dispute-resolution query pattern; isolates from `sessions` retention. |

Cross-partition queries are **forbidden** in the hot path. Any cross-partition query requires an ADR.

### 4.2 TTL Usage

- **`sessions` uses per-item TTL** set on transition to terminal state (`Scored`/`Expired`), per ADR 006 + spec 008 §2.1. The container's `defaultTtl` is `null`; the per-item `ttl` field is written on the conditional update that flips status. **Do NOT set container `defaultTtl` on `sessions`** — it would expire `Active` sessions mid-quiz.
- **`audit` uses container-level `defaultTtl`** (365 days hot per ADR 006) because every row's retention is uniform.
- **Pattern**: per-container TTL when retention is uniform; per-item TTL when retention is gated on a state transition.
- Both values are owned by `infra/` Bicep and documented in [`docs/retention.md`](./retention.md). Changing either is a P1 change requiring `@security` codeowner review.
- `audit` retention MUST be longer than `sessions` retention (SEC-014 / TASK-133). A periodic assertion script verifies this against the live containers.

### 4.3 Indexing Policy Guidance

- Exclude paths the system never queries (`/*` excluded by default, include only what's queried). Default Cosmos indexing on JSON documents is wasteful.
- For `sessions`, index `/status`, `/startedAt`, `/userId/?` (PK is implicitly indexed). Do NOT index `/answers/*` deeply.
- Composite indexes only when the query plan demands them; document the query and the index together in the repository file.
- Indexing policy is owned by `infra/` Bicep, not by application code. No `replace_container` calls from `src/`.

### 4.4 Concurrency Control

- **All** mutating writes on `sessions` MUST use conditional writes (`if_match=<etag>`). Unconditional `replace_item` on `sessions` is **forbidden** outside of bootstrap.
- The natural concurrency key is `(session_id, question_id)` — etag captured at read, asserted at write.
- On `412 Precondition Failed`: refresh, replay the validation, retry **once**. Multiple retries on the same etag are wasted work; on a second loss, return `FlintConflictError` (the caller idempotent-retries via the tool contract).

### 4.5 Etag / Idempotency Usage

- Etags are 🔴. Never log, never include in tool return JSON, never expose to the LLM.
- The repository method signature MUST surface the etag to the service layer explicitly:

#### GOOD

```python
async def get_session(self, session_id: SessionId) -> tuple[SessionDoc, Etag]: ...
async def replace_session_if_match(
    self, session: SessionDoc, etag: Etag
) -> SessionDoc: ...
```

#### BAD

```python
async def update_session(self, session: SessionDoc) -> SessionDoc:
    # Hidden etag handling. Where did the etag come from? Race-prone.
    await self._container.replace_item(session.id, session.model_dump())
```

### 4.6 Retry Policy

- Use the Azure Cosmos SDK's built-in retry policy for transient `429`/`5xx`. Override the defaults centrally in `src/data/cosmos_repository.py`:
  - `max_retry_attempts_on_throttled_requests=5`
  - `max_retry_wait_time_in_seconds=10`
- Wrap idempotent operations with `tenacity.retry` only when the SDK policy is insufficient. Conditional writes (`I-K`) do NOT need additional retries — the tool contract handles it.
- A `429` rate > 1% over a 5-minute window triggers an alert (TASK-145). Do not paper over it with longer retries.

---

## 5. Azure AI Search Standards

### 5.1 Language Analyzers

- **One record per `(logical_id, language)`** pair (see [`003-data §2.1`](../specs/003-data-contracts.md)). Per-record analyzer field name pattern: `text` uses `language`-derived analyzer (`fr.microsoft`, `es.microsoft`, `en.microsoft`).
- The `language` field is `filterable: true, facetable: true, retrievable: true`. **Every** runtime query filters by `language`. Queries without a `language` filter are a P1 bug (caught by the repository layer — guard clause raises `FlintValidationError`).
- Synonym maps are per-language; updating synonyms requires a reindex on the affected language only.

### 5.2 Schema Versioning

- Index schema lives in Bicep + a JSON contract under `infra/search/`. Schema changes follow this protocol:
  1. ADR proposing the schema change.
  2. New index name `questions-v{N+1}` deployed in parallel.
  3. Reindex into the new index.
  4. Application flips the index alias (`questions` → `questions-v{N+1}`) via App Configuration.
  5. Old index decommissioned after a soak period (default: 14 days).
- In-place schema changes that the SDK allows (adding a non-key field) STILL require the version-and-flip protocol — we do not "evolve" production indexes silently.

### 5.3 Filtering Rules

- Mandatory filters on every runtime query: `language eq '<code>'`. Additional filters: `topic`, `difficulty`, `tags/any(...)` as needed.
- OData filter strings MUST be built via a typed builder in `src/data/question_search.py` — never via f-string concatenation of user input. Filter injection (analogous to SQL injection) is a Q1 bug.
- `correct_answer` is never returned via `select=...`. The two-method split (`get_question_view` returns the projection allowlist; `get_answer_key` is the only path that includes `correct_answer`) is the structural enforcement (ADR-005).

#### BAD

```python
filter = f"language eq '{lang}' and topic eq '{user_topic}'"  # injection-prone
```

#### GOOD

```python
filter = SearchFilter().eq("language", lang).eq("topic", topic).build()
```

### 5.4 Semantic Search Guidance

- Semantic ranker / semantic captions are NOT in the v1 hot path. Their latency exceeds the NFR-001 budget for voice. If you need fuzzy topic matching, do it offline as a content-authoring step (see [`docs/content-governance.md`](./content-governance.md)).

### 5.5 Query Performance

- `start_quiz` issues exactly **one** AI Search filtered query for the candidate ID set, then **one** point read per question fetched. Multi-page result aggregation is forbidden in the hot path.
- Tune `top`, `select`, and `skip` to minimize bytes over the wire. `select` MUST be an explicit allowlist — never `select=*`.
- Index size is small (≤ 100k items in v1). If it crosses 100k, revisit the partition / replica plan ([`002-arch §11`](../specs/002-system-architecture.md)).

---

## 6. API Standards

### 6.1 Pydantic Models Are Mandatory

- Every tool input and output is a Pydantic `BaseModel` (not `dataclass`, not `TypedDict`, not a bare `dict`).
- Models live in `src/data/models.py`. They MUST mirror the wire schema in [`008-api-contracts.md`](../specs/008-api-contracts.md) exactly — field names, types, optionality, defaults.
- Use `model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)` on tool-boundary models. Silent extra fields are a contract violation (a typo'd attribute should fail loudly).
- Aliases bridge `snake_case` (Python / tool wire) and `camelCase` (Cosmos): `Field(alias="userId", serialization_alias="userId")`.

### 6.2 Schema Versioning

- Each tool contract carries a `schema_version: Literal[1]` field (start at 1). Breaking changes increment the version and create a new contract; we do not silently mutate.
- Cosmos documents carry `schemaVersion: int`. Repository read paths handle the current version directly; older versions go through a versioned migrator in `src/data/migrations/`. Never branch on `schemaVersion` inside business logic.

### 6.3 Validation Rules

- Validate at the boundary: tool entry point Pydantic-parses the input; repository entry point Pydantic-parses the document on read. **Internal functions trust their typed inputs** — no defensive re-validation.
- Custom validators in Pydantic for domain rules:
  - `SupportedLanguageCode` — validates against the SEC-010 ISO 639-1 allowlist (used by `set_language`, `start_quiz`, `sessions.language`).
  - `DetectedLanguageCode` — validates only as ISO 639-1 (any two-letter code); used by `users.detectedLanguage` per spec 008 §2.2 to record an out-of-allowlist detection for the fallback decision.
  - `OptionKey` — single uppercase letter matching `^[A-Z]$` per spec 008 §0.2 / §4.1; runtime-bounded by `len(question.options) ≤ 26`. **Do NOT narrow to `Literal["A".."E"]`** — today's bank uses A–D but the contract permits A–Z, and a future six-option question would silently fail.
  - `QuestionId` matches the pattern in [`008-api §0.2`](../specs/008-api-contracts.md).
- A failed boundary validation becomes a `FlintValidationError`, mapped to the error envelope's `validation` class.

### 6.4 Retry-Safe Endpoints

- Every tool MUST declare its idempotency class in the docstring **and** in a registry consulted by the dispatcher (TASK-070):

```python
TOOL_REGISTRY: Mapping[str, ToolSpec] = {
    "submit_answer": ToolSpec(handler=submit_answer, idempotency="I-K", hot_path=True),
    ...
}
```

- The dispatcher enforces: an `I-K` tool retried within a session window for the same key returns the cached in-flight result (TASK-070 / GOV-012). Implementations rely on this — no per-tool retry book-keeping.

### 6.5 Structured Error Responses

Per [`008-api §4.2.1`](../specs/008-api-contracts.md) — the envelope shape is authoritative there. Python implementation:

```python
class ErrorEnvelope(BaseModel):
    """Mirrors specs/008-api-contracts.md §4.2.1 exactly. The envelope itself
    may carry 🟡 fields (message_dev, detail) for server-side telemetry; the
    renderer is the gatekeeper that surfaces ONLY message_user to LLM context.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str                                # 🟢 stable enum (spec 008 §4.2.2)
    message_user: str                        # 🟢 localized, TTS-shaped, the only LLM-visible string
    message_dev: str | None = None           # 🟡 server-only diagnostic; never to LLM
    correlation_id: str | None = None        # 🟢 W3C traceparent-derived ID for support
    retryable: bool                          # 🟢
    retry_after_ms: int | None = None        # 🟢 honored by SDK retry (spec 008 §4.6)
    detail: dict[str, object] | None = None  # 🟡 server-only structured data (e.g., active_session_id)
```

**Critical:** the presence of `message_dev` and `detail` on the envelope does **NOT** relax SEC-001. They are populated for App Insights correlation via `correlation_id`. The **error rendering layer** (see [`008-api §6.4`](../specs/008-api-contracts.md)) is the single point that surfaces fields to the LLM, and it surfaces only `message_user`. A `logger.info(f"{error}")`-style interpolation that dumps the envelope is forbidden; lint rule `LOG001` enforces.

`correlation_id` is the same value the spec calls `trace_id` historically; spec 008 §4.2.1 has been updated to use `correlation_id` as the canonical name aligned with OTel.

---

## 7. Security Standards

### 7.1 Managed Identity Only (SEC-004 / TASK-120)

- **Every** Azure SDK client MUST construct with `DefaultAzureCredential` (with `ManagedIdentityCredential` preferred in production).
- **No** connection strings, keys, SAS tokens, or shared access signatures may appear in code, env files, Bicep outputs, or CI logs. CI greps for `AccountKey=`, `SharedAccessSignature=`, `ApiKey=`, `AccountEndpoint=...;AccountKey=` and fails on match (TASK-120).
- The only documented exception is App Insights instrumentation/connection string (per Microsoft guidance); it is documented inline in CI config.

### 7.2 No Secrets in Code

- Secrets live in **Key Vault**, accessed via `src/data/keyvault_client.py` (TASK-122). All consumers go through the wrapper; no direct `SecretClient` instantiation elsewhere.
- The repository contains **zero** secret values. Pre-commit hook runs `detect-secrets` or equivalent; CI re-runs it.
- The `.env` file pattern is reserved for local dev only (`.env.example` checked in; `.env` is `.gitignore`d). It MUST NOT contain production values.

### 7.3 RBAC Enforcement (SEC-005 / TASK-121)

- The runtime UAMI's role assignments are **least-privilege**, scoped per resource:
  - `Cosmos DB Data Contributor` on the agent's Cosmos account only.
  - `Search Index Data Reader` on the questions index.
  - `Key Vault Secrets User` on the agent's Key Vault.
- The seed UAMI is **separate** and has `Search Index Data Contributor`. The runtime UAMI MUST NOT have write on Search.
- Subscription-scope assignments, `Owner`, `Contributor`, `User Access Administrator` on runtime UAMI are **forbidden**. CI post-provision hook asserts.

### 7.4 Structured Audit Logging (SEC-014 / TASK-141)

- Grading correctness emits to **two sinks** with different shapes ([`008-api §4.5`](../specs/008-api-contracts.md)):
  - **App Insights** `grading_event` — 🟢 dimensions only: `sessionId`, `questionId`, opaque `userId`, `language`, `received` (normalized key), `verdict`, `channel`, `scoreDelta`, `latencyMs`, `timestamp`.
  - **Cosmos `audit`** — server-only, includes 🟡 `expected` and `receivedRaw`. RBAC-restricted.
- App Insights events MUST NOT contain `expected` or `receivedRaw`. Asserted by TEST-010.
- Audit events are emitted **only on the successful write path** — not on idempotent no-ops. Double-emission is a SEC-006 regression.

### 7.5 PII Handling

- User identity is an **opaque Entra OID** in code (`user_id: UUID`). Display name, email, locale-from-token, etc. are NOT stored in Cosmos and NOT sent to App Insights.
- Voice transcripts and text transcripts are PII. App Insights retention follows [`docs/retention.md`](./retention.md) (default: 30 days transcripts, 90 days grading events).
- The right-to-erasure cascade lives in `src/data/erasure.py` (TASK-134) and is invoked by support tooling — never by an agent tool, never by user input.

### 7.6 App Insights Query Surface PII Discipline

App Insights is the **broad-access telemetry surface**. The grading-correctness dashboard, voice hot-path dashboard, and ad-hoc KQL queries are read by a wider engineering audience than the `audit` container's RBAC scope. Discipline:

- **Today's `userId`** is an opaque Entra OID (UUID). Treated as 🟢 because it is non-reversing to PII without a separate Entra lookup that is itself RBAC-controlled.
- **If `userId` ever migrates to a non-opaque internal ID** (e.g., a numeric customer ID), the existing `grading_event` and `agent.*` events become PII-bearing without any code change. **Forecast and prevent**: a CI lint (TASK-149 extension) scans event-emission code for any `userId`-typed value being added to a customDimension, and requires an inline `# AL-007 OID-only` comment. Migrations that change the semantic content of `userId` require a corresponding schema-version bump on the event taxonomy.
- **Ad-hoc query review**: any saved KQL query that returns rows containing `userId` requires `@security` codeowner review on the workbook PR. The pattern lint `forbid-userid-row-export` blocks `project userId, ...` patterns in shared workbooks without an explicit allowlist annotation.
- **PII redaction on Log Analytics retention transition**: at 30 days, transcript-bearing `customEvents` are PII-scrubbed; only the structural fields (event name, counts, latencies) are retained per ADR 006. The scrubber runs as a Log Analytics workspace policy, not as application code.

### 7.7 Transcript Retention

- Cosmos `sessions` TTL is set per terminal state (TASK-050 / [`docs/retention.md`](./retention.md)).
- `audit` retention diverges from `sessions` (SEC-014) — longer, to allow dispute triage after session expiry.
- Periodic CI assertion (`docs/retention.md` script) verifies live retention matches documentation.

### 7.8 Injection Protection

- **OData / SQL injection**: filter strings built via typed builders (see §5.3). Repository methods accept typed args, not raw strings.
- **Prompt injection (answer leakage)**: structurally impossible by SEC-007 — the model never sees the answer key. Tests run a multilingual + encoded-variant corpus on every release (`tests/test_prompt_injection.py`, TASK-126; `tests/test_injection_corpus.py`, TEST-023).
- **Tool-call injection**: the dispatcher's tool-allowlist (TASK-070 / GOV-010) rejects any tool name not in the registered five. Encoded names, namespaced variants, and Unicode lookalikes all fail closed.
- **Cross-session data**: every repository read takes `session_id` and re-verifies ownership against the authenticated principal. No cross-session reads from a tool path — even on operator request, the operator goes through a separate support tool.

---

## 8. Observability Standards

### 8.1 Structured Logs

- All logs structured (see §1.10). No free-form messages with embedded data.
- Stable event names use dotted hierarchy: `submit_answer.started`, `submit_answer.persisted`, `submit_answer.idempotent_replay`, `submit_answer.failed`.
- Forbidden in any log: 🟡 / 🔴 fields (see §0.3).

### 8.2 Correlation IDs

- A `correlation_id: UUID` is generated at session start and propagated through:
  - Every log line (via `LoggerAdapter`).
  - Every OTel span (as `flint.correlation_id` attribute).
  - Every `ErrorEnvelope` returned to the caller.
  - Every Cosmos / Search request's `client_request_id`.
- Cross-process boundaries propagate via OTel `traceparent` (W3C Trace Context). Manual generation is the exception, not the rule.

### 8.3 Distributed Tracing (TASK-140 / TASK-144)

- OpenTelemetry via `azure-monitor-opentelemetry`. Initialised in `src/agent/composition.py` boot path.
- Required span set: `tool.list_topics`, `tool.set_language`, `tool.start_quiz`, `tool.submit_answer`, `tool.get_results`, `cosmos.read`, `cosmos.conditional_write`, `search.query`, `search.get_question_view`, `search.get_answer_key`.
- Required attributes (where applicable): `flint.language`, `flint.channel`, `flint.verdict`, `flint.session_id`, `flint.correlation_id`.
- **Forbidden** attributes (lint-blocked at PR time): `correct_answer`, `answer_key`, `expected`, `received_raw`, `_etag`. Any span attribute with one of these names fails the build (TASK-144).

### 8.4 Telemetry Naming Conventions

| Surface | Convention | Example |
|---------|------------|---------|
| Log event name | `<tool_or_module>.<event>` (snake_case) | `submit_answer.persisted` |
| OTel span name | `<layer>.<operation>` (snake_case, lowercased) | `cosmos.conditional_write` |
| OTel attribute | `flint.<dim>` (lower-snake) | `flint.language` |
| App Insights custom event | `<domain>.<event>` (snake_case) | `grading_event`, `agent.injection_detected` |
| Metric (custom) | `flint_<noun>_<unit>` (lower-snake) | `flint_voice_latency_ms` |

The `agent.*` governance event taxonomy is defined in TASK-149. Adding a new `agent.*` event requires an entry in that table and a CI lint update.

### 8.5 Grading Event Logging

- `grading_event` is emitted **once per persisted answer**, on the successful write path of `submit_answer`. Never on idempotent no-op (the second call returns the cached verdict without emit).
- Dimensions per [`008-api §4.5.1`](../specs/008-api-contracts.md): `sessionId`, `questionId`, `userId`, `language`, `received`, `verdict`, `channel`, `scoreDelta`, `latencyMs`, `timestamp`.
- **Explicitly excluded**: `expected`, `receivedRaw`. Adding either is a SEC-001 regression — TEST-010 asserts absence.

### 8.6 Latency Tracking

- Every tool span carries `flint.latency_ms` (computed from span duration; redundant but cheap and queryable without span math in KQL).
- Voice hot-path latencies (STT first-final, TTS first-byte, tool round-trip in `channel == 'voice'`) surface on the `Quiz Voice — Hot Path` workbook (TASK-142). The p95 alert threshold is **300 ms** for tool round-trip (NFR-001 / TASK-145).
- Any code path with a documented latency budget ([`008-api §1.1`](../specs/008-api-contracts.md)) MUST have a span; an unspanned hot-path operation is a P2 (caught in review).

---

## 9. Testing Standards

### 9.1 Required Test Coverage

- **Target** (SHOULD; tracked in TASK-TBD-COV1): minimum **85% line coverage** on `src/`, enforced by `pytest-cov` thresholds in CI. Higher floors per-module:
  - `src/agent/tools.py`: **95%** — every branch through every tool.
  - `src/data/erasure.py`: **100%** — every path through the cascade, plus negative-auth.
  - `src/agent/answer_normalizer.py`: **95%** — per-language regression matrix.
- Coverage is a **floor, not a target**. A test that exists only to bump coverage is a bug.
- **Enforcement status (2026-05-17)**: `pyproject.toml` `pytest --cov` threshold and CI gating are not yet wired. Until they are (TASK-TBD-COV1), reviewers SHOULD spot-check coverage on changed modules. Do not merge new tool code without exercising every branch through it.

### 9.2 Deterministic Tests

- Tests MUST be deterministic. No `time.sleep`, no real network, no real LLM calls in the default suite.
- Use a **frozen clock** (`src/common/clock.py` injected via DI) — tests pass a `FrozenClock(at=datetime(...))`.
- The seeded RNG (per-session shuffle, [`002-arch §10`](../specs/002-system-architecture.md)) makes question ordering deterministic; tests assert on the ordering directly.
- Flaky tests are P1 — disable + open a ticket the same day. Don't `@pytest.mark.flaky`.

### 9.3 Integration Tests

- The **idempotency test** (TEST-007 / TASK-131 / `tests/test_idempotency.py`) MUST run against a real Cosmos emulator or a real test Cosmos account — never a mock. Mock-based idempotency tests are worthless because they don't exercise `ifMatch`.
- Integration tests use a separate suite (`tests/integration/`), separate CI stage, and a per-PR ephemeral resource group provisioned by Bicep.

### 9.4 Adversarial Prompt Tests

- `tests/test_prompt_injection.py` (TASK-126) and `tests/test_injection_corpus.py` (TEST-023): adversarial inputs across English, French, Spanish, plus encoded variants (base64, ROT13, leetspeak).
- Each scenario asserts: (a) no `correct_answer` string surfaces; (b) no prompt-content surfaces; (c) `agent.injection_detected` fires with hashed payload (TASK-149).
- New attack patterns ⇒ new test row. Run on every model upgrade (model behavior may regress).

### 9.5 Multilingual Tests

- Every test that exercises a tool or normalizer is parametrized over the supported languages:

```python
@pytest.mark.parametrize("language", ["en", "fr", "es"])
async def test_grading_partial_credit(language: LanguageCode, ...): ...
```

- A test that runs only in English on a multilingual path is incomplete and will be rejected in review.

### 9.6 Voice Tests

- `tests/test_tts_invariants.py` (TEST-024) asserts voice-channel rendering: no markdown, no raw URLs, "Option A:" framing, numeral expansion ≤ 100, acronym expansion.
- Answer-normalizer tests cover spoken variants per language ("the second one", "la deuxième", "la segunda", "letter B", "option B").
- End-to-end voice (TEST-005) is a **smoke test**, not a unit test — it runs against the deployed Realtime endpoint in `dev`.

### 9.7 Replay Tests

- `submit_answer` replay test (TEST-007): N=20 duplicate calls in parallel ⇒ exactly one persisted answer, exactly one `audit` row, exactly one `grading_event` emission.
- `start_quiz` idempotency-via-session-lookup test: duplicate calls return the same `session_id`.
- Run as part of the integration suite against real Cosmos.

---

## 10. Git Standards

### 10.1 Branch Naming

- `main` is the only long-lived branch. Protected; merges via PR only; squash-merge with the PR title as the commit message.
- Feature branches: `feat/<short-kebab-case>` (e.g., `feat/answer-normalizer-french-variants`).
- Fix branches: `fix/<short-kebab-case>`.
- Spec / docs branches: `docs/<short-kebab-case>`.
- Infrastructure: `infra/<short-kebab-case>`.
- Spec-Kit feature branches: `NNN-feature-slug` (zero-padded, sequential).

### 10.2 Commit Conventions

- **Conventional Commits** (`<type>(<scope>): <subject>`). Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `infra`, `sec`. Enforcement via `commitlint` is SHOULD until the `.pre-commit-config.yaml` and CI `commitlint` job are wired (TASK-TBD-COMMIT1); reviewers spot-check the message in the meantime.
- Subject is imperative, ≤ 70 chars, no trailing period.
- Body wraps at 100 chars; explains **why**, not what.
- Reference spec / requirement IDs in the body: `Refs: SEC-006, NFR-002, TASK-131`.
- Breaking changes: `feat(tools)!: rename submit_answer payload field` + `BREAKING CHANGE:` footer.
- **Never** include secrets, full stack traces, or 🟡 fields in commit messages.

#### GOOD

```
feat(tools): add coverage-fallback consent prompt to start_quiz

Implements GOV-025: when the requested topic has no coverage in the
requested language, surface an explicit consent prompt before serving
the closest available language. Refuses silent cross-language fallback.

Refs: GOV-024, GOV-025, TEST-022, TASK-189
```

### 10.3 PR Review Expectations

- Every PR MUST:
  - Reference at least one spec / requirement / task ID in the description.
  - Pass CI green (lint, mypy --strict, unit tests, integration tests on label `run-integration`).
  - Include or update tests for changed behavior.
  - Update [`docs/llm-boundary.md`](./llm-boundary.md) if any tool surface, prompt layer, or telemetry shape changes.
  - Update [`docs/retention.md`](./retention.md) if any TTL or retention window changes.
  - Have at least one approving review from a code owner (CODEOWNERS-enforced).
- Security-tier changes (SEC-*, ADR-005, anything touching `correct_answer` paths) require a security review from a `@security` codeowner.
- AI-generated PRs SHOULD declare provenance in the description (see §11). A PR template with the provenance field is tracked in TASK-TBD-PRTPL1; until merged, reviewers ask in the PR comments and record assistant usage in the merge commit.

### 10.4 ADR Requirements

- An ADR is mandatory for:
  - Adding/removing an Azure service.
  - Adding/removing a tool from the agent surface.
  - Changing the data plane (Cosmos partition, AI Search schema).
  - Changing the security model boundary (LLM-OK vs SERVER tier of a field).
  - Choosing a new framework / runtime (e.g., introducing LangGraph in v2).
- ADRs follow the existing template in [`adr/`](../adr/). Title: `NNN-decision-name.md`, numbered sequentially. Status flows: `Proposed → Accepted → Superseded by NNN`.
- An ADR is **not** required for: bug fixes, dependency bumps without behavior change, doc-only changes, internal refactors that preserve all public contracts.

---

## 11. AI-Generated Code Governance

These standards apply to **any** code authored or significantly modified by an AI assistant (Claude Code, Copilot, Cursor, etc.).

### 11.1 AI-Generated Code MUST NOT Bypass Contracts

- AI-generated code is held to **the same** standards as human-authored code in every section above. There are no exemptions for "the model wrote it".
- The model MUST NOT introduce a new tool, repository, exception class, or telemetry event without updating the corresponding spec / ADR / task.
- The model MUST NOT silently widen a Pydantic model's `extra` policy, remove a validation, or downgrade a `Literal` to a `str`. These are contract changes and require an explicit PR description note.

### 11.2 AI-Generated Code MUST NOT Invent Schemas

- Field names, types, and structure MUST come from [`specs/008-api-contracts.md`](../specs/008-api-contracts.md). If the spec doesn't have it, the spec changes first; code follows.
- Inventing fields ("looks like it needs a `version` field, let me add one") is a P1 defect even if the code passes tests. Tests of invented fields are not evidence of correctness.
- Cosmos document shape, AI Search index schema, and tool wire shape are **authoritative externally** — code mirrors, never proposes.

### 11.3 AI-Generated Code MUST Reference Specs

- Every AI-authored module / class / public function carries a docstring citation to the relevant spec/ADR/task ID, e.g., `See SEC-006, NFR-002, TASK-131`.
- If the assistant cannot find a spec reference for a behavior it is adding, that is a signal that the spec is missing — open a spec PR first, code PR second.
- PR descriptions for AI-assisted PRs MUST list the spec IDs the change implements or modifies.

### 11.4 All AI-Generated Code Requires Validation

Before merge, AI-generated code MUST pass — and a human reviewer MUST verify — the following:

| Check | How |
|-------|-----|
| Lint clean | `ruff check`, `black --check`, `mypy --strict` |
| Tests added/updated | New behavior ⇒ new test row (multilingual where applicable) |
| Spec alignment | Reviewer confirms field names, types, error codes match `specs/008` |
| Security boundary | If touching `src/agent/tools.py` or `src/data/question_search.py`, TEST-006 + TEST-018 run green; reviewer confirms no new path to `correct_answer` |
| Telemetry hygiene | New events / spans in TASK-149 / TASK-144 lint-checked; no 🟡/🔴 attributes |
| ADR coverage | New service/tool/schema change ⇒ ADR linked in PR |
| Provenance declared | PR description names the assistant + the prompt scope |

### 11.5 Failure Modes to Watch For (Reviewer Checklist)

The following are common AI-generated regressions; reviewers should look for them explicitly:

- **Invented tool name** — anything outside the registered five (GOV-010). Fails dispatcher, but should also fail review.
- **`correct_answer` in tool return** — even commented out or under a feature flag. P0.
- **Synthesized explanation text** — explanations come from the bank for the active language; not from the model (GOV-031, TEST-020).
- **Silent language switch** — mid-session language change without `set_language` + consent (GOV-024 / GOV-025, TEST-022).
- **English-by-default refusal** — refusal copy not sourced from the active phrasing block (GOV-052 / GOV-072, TEST-021).
- **Cross-partition Cosmos query** — added "because it was easier than refactoring".
- **`except Exception:`** at any layer that isn't the dispatcher.
- **Bare `dict` in a public signature** — usually a stand-in for a missing Pydantic model.
- **New `os.environ[...]` outside `config.py`** — bypasses configuration management.

If any of these slip through review, the fix is **not** a patch on top — it is a revert + redo. This is the only sustainable posture.

---

## 12. Folder & Naming Conventions

### 12.1 Repository Layout (authoritative)

```
flint-quiz/
├── adr/                          # Architectural Decision Records (NNN-name.md)
├── docs/                         # Operational docs (this file lives here)
├── infra/                        # Bicep modules; no application code
├── specs/                        # NNN-name.md authoritative specifications
├── src/
│   ├── agent/
│   │   ├── prompts/              # Layered prompt files (identity, contract, lang/, compose.py)
│   │   ├── answer_normalizer.py
│   │   ├── composition.py        # DI composition root
│   │   ├── quiz_agent.py         # MAF agent entrypoint
│   │   ├── tools.py              # The five tools — no other tools
│   │   └── tts_shaper.py
│   ├── common/                   # Cross-cutting (config, exceptions, logging, clock, telemetry)
│   ├── data/
│   │   ├── cosmos_repository.py
│   │   ├── erasure.py            # GDPR cascade (TASK-134)
│   │   ├── keyvault_client.py
│   │   ├── models.py             # Canonical Pydantic models
│   │   └── question_search.py    # Two-method split (ADR-005)
│   └── seed/
│       └── seed_index.py
├── tasks/                        # Implementation task packs (NNN-name.md)
└── tests/
    ├── integration/              # Real Cosmos / Search; separate CI stage
    └── *.py                      # Deterministic unit tests
```

### 12.2 Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Module file | `snake_case.py` | `answer_normalizer.py` |
| Test file | `test_<subject>.py` | `test_no_answer_leakage.py` |
| Class | `PascalCase` | `SessionsRepository` |
| Function / method | `snake_case` | `submit_answer` |
| Constant | `UPPER_SNAKE_CASE` | `MAX_RETRY_ATTEMPTS` |
| Pydantic model | `PascalCase`, noun (no `Model` suffix) | `SessionDoc`, `QuestionView` |
| Custom exception | `PascalCase`, `Error` suffix | `AnswerLeakageError` |
| OTel span | `<layer>.<operation>` | `cosmos.conditional_write` |
| OTel attribute | `flint.<dim>` | `flint.language` |
| App Insights event | `<domain>.<event>` | `grading_event`, `agent.injection_detected` |
| Spec doc | `NNN-kebab-case.md` | `008-api-contracts.md` |
| ADR | `NNN-kebab-case.md` | `005-tool-boundary-prevents-answer-leakage.md` |
| Task pack | `NNN-domain.md` | `007-security.md` |
| Requirement ID | `<PREFIX>-NNN` | `SEC-006`, `GOV-025`, `TASK-131` |

### 12.3 File-Level Discipline

- One Pydantic model per concept; co-locate related models in the same module (`src/data/models.py`).
- One repository class per Cosmos container or Search index. Don't bundle.
- One test class / module per public surface; tests mirror source layout (`src/agent/tools.py` ↔ `tests/test_tools.py`).
- Files over **600 lines** SHOULD trigger a refactor PR within the next sprint. Long files are a refactor signal; this is enforced by review judgment, not by a hard CI cap.

---

## Appendix A — Enforcement Matrix

| Standard | Enforced by | Where |
|----------|-------------|-------|
| Typing (§1.2) | `mypy --strict` | CI |
| Format (§1.4) | `black --check`, `ruff check` | pre-commit + CI |
| Imports (§1.5) | `ruff (I)` | pre-commit + CI |
| Layer dependencies (§2.1) | `import-linter` (config in `pyproject.toml [tool.importlinter]`) | CI |
| Tool answer-leakage (§3.1) | `tests/test_no_answer_leakage.py` (TEST-006) | CI per-PR |
| `get_answer_key` import restriction (§2.4) | `tools/lint/check_answer_key_import.py` — AST visitor over `src/agent/tools.py`. Fails CI if any `ImportFrom` of `get_answer_key` resolves outside the `submit_answer` function body. Authoritative tool; renames of `get_answer_key` must update the lint's symbol pin in the same PR. (TASK-125) | CI per-PR |
| Prompt redaction (§3.4) | `tests/test_prompt_redaction.py` (TEST-018) | CI per-PR |
| TTS invariants (§3.9) | `tests/test_tts_invariants.py` (TEST-024) | CI per-PR |
| Cosmos conditional write (§4.4) | `tests/test_idempotency.py` (TEST-007) | Integration CI |
| `language` filter on Search (§5.1) | Repository guard clause + unit test | CI per-PR |
| No connection strings (§7.1) | CI grep (TASK-120) | CI per-PR |
| RBAC scope (§7.3) | Post-provision Bicep hook (TASK-121) | Deploy |
| Telemetry redaction (§7.4) | TEST-010 + lint on span attributes (TASK-144) | CI per-PR |
| Conventional commits (§10.2) | `commitlint` | pre-commit + CI |
| Coverage floor (§9.1) | `pytest-cov` thresholds | CI |

---

## Appendix B — Glossary

| Term | Definition |
|------|------------|
| **MAF** | Microsoft Agent Framework (Python) — see ADR-001. |
| **Foundry** | Azure AI Foundry — hosts the MAF agent and the Realtime endpoint. |
| **Tool** | A Python function exposed to the MAF agent; one of the registered five. |
| **🟢 / 🟡 / 🔴** | Field sensitivity tier — see §0.3. |
| **P0 / P1 / P2** | Severity tier — see §0.2. |
| **Idempotency class** | `R` / `I-U` / `I-K` / `I-S` — see [`008-api §1.2`](../specs/008-api-contracts.md). |
| **Composed prompt** | The four-layer system prompt assembled at session start (GOV-001). |
| **Session frame** | Layer 4 of the composed prompt; carries `session_id`, channel, language, current index. |
| **`AnswerKey`** | Server-only dataclass containing `correct_answer`; never serialized to JSON. |
| **`QuestionView`** | LLM-safe Pydantic model; **structurally** has no `correct_answer` field. |
| **Audit-of-audit** | A pseudonymized event recording that an erasure cascade ran — itself an audit record. |
