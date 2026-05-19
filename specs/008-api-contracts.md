# 008 — API & Tool Contracts

- **Version**: v1.0 (authoritative wire-level contract; supersedes `003-data-contracts.md` for protocol details)
- **Last reviewed**: 2026-05-17
- **Owner**: Platform + Security
- **Status**: Accepted

This document is the **authoritative wire-level contract** for every tool, datastore, and event surface in the system. It is the artifact a reviewer reads to verify SEC-001/SEC-002 (no answer leakage), NFR-002/SEC-006 (idempotent grading), and SEC-009 (the "what does the LLM see" boundary).

Scope:

- Tool request/response schemas (the five MAF tools).
- Cosmos DB document schemas (sessions, users, topics, audit).
- AI Search index schema (questions) + projection contracts.
- Validation rules, error contracts, retry/idempotency rules.
- Session lifecycle state machine + transition contracts.
- Voice normalization contracts.

Cross-references: [001-product-requirements](./001-product-requirements.md), [002-system-architecture](./002-system-architecture.md), [003-data-contracts](./003-data-contracts.md), [004-agent-behavior](./004-agent-behavior.md), [005-security-model](./005-security-model.md), [006-testing-strategy](./006-testing-strategy.md), [007-operational-runbook](./007-operational-runbook.md).

---

## 0. Conventions

### 0.1 Field-Level Sensitivity Tiers

Every field in this document is classified by where it is allowed to flow:

| Tier        | Marker        | Definition                                                                                                |
| ----------- | ------------- | --------------------------------------------------------------------------------------------------------- |
| `LLM-OK`    | 🟢            | May appear in tool returns that pass through the agent's LLM context.                                     |
| `SERVER`    | 🟡            | Read/written by tool code, **never** placed in any string returned to the agent. Server-only.             |
| `SECRET`    | 🔴            | Sensitive material (credentials, etag tokens used for auth). Never logged in cleartext, never to the LLM. |

**SEC-001 reduces to a typed rule**: any field tagged 🟡 or 🔴 that crosses into a tool response visible to the agent is a contract violation and a P0 incident (see [007-operational-runbook §9](./007-operational-runbook.md)).

### 0.2 Type Notation

JSON-flavored TypeScript with a few extensions:

- `ISO8601` — RFC 3339 UTC timestamp string (`"2026-05-17T12:34:56.789Z"`).
- `ISO639-1` — two-letter language code; validated against the App Configuration allowlist (SEC-010).
- `UUID` — RFC 4122 v4.
- `Etag` — opaque Cosmos `_etag` string. 🔴.
- `LogicalId` — pattern `^[a-z0-9-]{3,64}$`. Stable across languages (e.g., `az-net-0042`).
- `QuestionId` — pattern `^[a-z0-9-]{3,64}-(en|fr|es|[a-z]{2})$`. Per-language record (e.g., `az-net-0042-fr`).
- `OptionKey` — single uppercase letter `A`..`Z`.

### 0.3 Error Envelope

Every tool returns a discriminated union: `{"ok": true, "data": ...}` on success, `{"ok": false, "error": ...}` on failure. The `error` shape is in §6.1. The LLM only ever sees the user-facing rendering of `data` or `error.message_user` — never internal codes, stack traces, or 🟡/🔴 fields.

### 0.4 Naming

Tool inputs and outputs use `snake_case`. Cosmos documents preserve Cosmos system-field casing (`id`, `_etag`, `_ts`) and use `camelCase` for everything else (matches the existing [003-data-contracts §4](./003-data-contracts.md) examples).

---

## 1. Tool Contracts

The agent's only side effects flow through these five tools. Each contract below defines: **inputs**, **outputs**, **validation**, **security restrictions**, **failure modes**, **idempotency class**, **latency budget**, and a **JSON example**.

### 1.1 Latency Budget Table (NFR-001)

| Tool             | Voice p95 target | Text p95 target | Rationale                                                  |
| ---------------- | ---------------- | --------------- | ---------------------------------------------------------- |
| `list_topics`    | 100 ms           | 250 ms          | Cosmos point-read of cached small doc; cacheable.          |
| `set_language`   | 80 ms            | 200 ms          | Single Cosmos upsert on `users`.                           |
| `start_quiz`     | 300 ms           | 600 ms          | AI Search filtered query + Cosmos insert + first Q fetch.  |
| `submit_answer`  | 250 ms           | 500 ms          | Search point-read (answer key) + Cosmos conditional write. |
| `get_results`    | 150 ms           | 400 ms          | Cosmos point-read of session.                              |

Anything outside these budgets in voice mode is a violation of NFR-001 and triggers the voice dashboard alert ([007-operational-runbook §2.3](./007-operational-runbook.md)).

### 1.2 Idempotency Classes

| Class | Definition                                                                                                            | Tools                                |
| ----- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| `R`   | **Read-only**. Safe to retry unconditionally; no state change.                                                        | `list_topics`, `get_results`         |
| `I-U` | **Idempotent-upsert**. Repeat with same input → same final state. No idempotency key required.                        | `set_language`                       |
| `I-K` | **Idempotent via key**. Repeat with same input → same final state, enforced by conditional write. **Non-negotiable.** | `submit_answer`                      |
| `I-S` | **Idempotent via session lookup**. Repeat call within active session window returns the in-flight session.            | `start_quiz`                         |

`submit_answer`'s `I-K` is the SEC-006 / NFR-002 guarantee. See §4.4 for the conditional-write contract.

---

### 1.3 `list_topics`

**Purpose** — Return the catalog of available topics with localized labels and per-language counts. Used by the agent during topic selection.

**Idempotency**: `R` · **Auth**: caller's Entra ID · **Hot path**: yes.

#### Inputs

```ts
type ListTopicsInput = {
  language: ISO639-1;       // 🟢  required
  user_id?: UUID;           // 🟢  optional; for telemetry only
};
```

#### Validation

| Rule        | Behavior on failure                                       |
| ----------- | --------------------------------------------------------- |
| `language` in allowlist (SEC-010) | Return `E_INVALID_LANGUAGE` (§6.2). |
| `language` non-empty             | Return `E_INVALID_INPUT`.            |

#### Outputs

```ts
type ListTopicsOutput = {
  topics: Array<{
    topic_id: LogicalId;      // 🟢  e.g., "azure-networking"
    label: string;            // 🟢  localized for input.language
    count: number;            // 🟢  questions available in this language
    has_fallback: boolean;    // 🟢  true if zero in requested language but coverage exists in another
  }>;
  language: ISO639-1;         // 🟢  echo of the resolved language
};
```

#### Security restrictions

- Returned `topic_id` is a slug, not an internal Cosmos partition key.
- Does **not** return question IDs, counts of correct answers, difficulty histograms, or any per-question metadata. (SEC-001 boundary.)

#### Failure modes

| Code                 | HTTP analog | Retry?                          | User-visible?                                |
| -------------------- | ----------- | ------------------------------- | -------------------------------------------- |
| `E_INVALID_LANGUAGE` | 400         | No                              | Yes — agent re-prompts for valid language.   |
| `E_INVALID_INPUT`    | 400         | No                              | Yes — agent re-prompts.                      |
| `E_BACKEND_TRANSIENT`| 503         | Yes (§7) — exponential backoff. | No (agent says "one moment").                |
| `E_AUTH`             | 401/403     | No                              | No — surface to platform telemetry.          |

#### Example

Request:
```json
{ "language": "fr", "user_id": "8d2c9f70-..." }
```

Success:
```json
{
  "ok": true,
  "data": {
    "language": "fr",
    "topics": [
      { "topic_id": "azure-networking", "label": "Réseau Azure",    "count": 85, "has_fallback": false },
      { "topic_id": "azure-storage",    "label": "Stockage Azure",  "count": 60, "has_fallback": false },
      { "topic_id": "azure-identity",   "label": "Identité Azure",  "count": 0,  "has_fallback": true  }
    ]
  }
}
```

---

### 1.4 `set_language`

**Purpose** — Persist the user's preferred language (FR-010, FR-014). Triggered explicitly ("respond in French") or implicitly after first-message detection (FR-011).

**Idempotency**: `I-U` · **Auth**: caller must own `user_id` · **Hot path**: yes.

#### Inputs

```ts
type SetLanguageInput = {
  user_id: UUID;            // 🟢  must match the caller's Entra ID subject
  language: ISO639-1;       // 🟢  required
};
```

#### Validation

| Rule                                                          | Behavior on failure          |
| ------------------------------------------------------------- | ---------------------------- |
| `language` in App Configuration allowlist (SEC-010)           | `E_INVALID_LANGUAGE`         |
| `user_id` matches authenticated principal                     | `E_AUTH_MISMATCH`            |
| Existing `users.{userId}` doc — upsert with optimistic etag   | Internal retry once on `412` |

#### Outputs

```ts
type SetLanguageOutput = {
  user_id: UUID;            // 🟢
  language: ISO639-1;       // 🟢
  updated_at: ISO8601;      // 🟢
};
```

#### Security restrictions

- `user_id` is never inferred from the conversation — it comes from the authenticated session principal. The model is never asked "what is your user id?".
- Language allowlist is the only acceptable filter. Free-form codes (e.g., `"klingon"`) are rejected before any datastore touches.

#### Failure modes

| Code                  | Retry?      | Notes                                                                |
| --------------------- | ----------- | -------------------------------------------------------------------- |
| `E_INVALID_LANGUAGE`  | No          | Agent should explain supported languages.                            |
| `E_AUTH_MISMATCH`     | No          | P1 telemetry — never expose the mismatch detail to the user.         |
| `E_BACKEND_TRANSIENT` | Yes         | Cosmos 429/503; SDK retry with exponential backoff (§7).             |
| `E_CONFLICT_ETAG`     | Yes (once)  | Concurrent update; retry after re-read.                              |

#### Example

```json
// Request
{ "user_id": "8d2c9f70-9b3a-4a3e-b3e2-aa1f2b3c4d5e", "language": "fr" }

// Response
{
  "ok": true,
  "data": {
    "user_id": "8d2c9f70-9b3a-4a3e-b3e2-aa1f2b3c4d5e",
    "language": "fr",
    "updated_at": "2026-05-17T12:34:56.789Z"
  }
}
```

---

### 1.5 `start_quiz`

**Purpose** — Create a new session, seed the deterministic shuffle (NFR-003), fetch the first question, return it to the agent **without the answer key** (SEC-001).

**Idempotency**: `I-S` (see §1.5.6) · **Auth**: caller's Entra ID; `user_id` must match · **Hot path**: yes.

#### 1.5.1 Inputs

```ts
type StartQuizInput = {
  user_id: UUID;                   // 🟢  authenticated principal
  topic: LogicalId;                // 🟢  e.g., "azure-networking"
  n: number;                       // 🟢  integer in [1, 50]
  language: ISO639-1;              // 🟢
  difficulty?: "easy" | "medium" | "hard" | "mixed";   // 🟢  default "mixed"
  time_limit_seconds?: number;     // 🟢  integer in [60, 3600]; default 600
  channel: "text" | "voice";       // 🟢  the channel currently in use
};
```

#### 1.5.2 Validation

| Rule                                                  | Behavior on failure              |
| ----------------------------------------------------- | -------------------------------- |
| `language` in allowlist                               | `E_INVALID_LANGUAGE`             |
| `topic` exists in `topics` container                  | `E_UNKNOWN_TOPIC`                |
| `n` integer, `1 ≤ n ≤ 50`                             | `E_INVALID_INPUT`                |
| `time_limit_seconds` integer, `60 ≤ t ≤ 3600`         | `E_INVALID_INPUT`                |
| Coverage exists for `(topic, language)`               | See §1.5.5 fallback contract     |
| User has no active session in same topic              | See §1.5.6 idempotency contract  |

#### 1.5.3 Outputs

```ts
type StartQuizOutput = {
  session_id: UUID;                // 🟢
  question: QuestionView;          // 🟢  see §1.5.4 — NEVER carries correct_answer
  index: number;                   // 🟢  1-based; first question is 1
  total: number;                   // 🟢  N
  language: ISO639-1;              // 🟢  resolved (may differ from requested under fallback)
  fallback_notice?: {              // 🟢  present iff resolved language ≠ requested
    requested: ISO639-1;
    resolved: ISO639-1;
    reason: "no_coverage_in_requested_language";
  };
  time_limit_seconds: number;      // 🟢
  question_started_at: ISO8601;    // 🟢
};
```

#### 1.5.4 `QuestionView` — the LLM-safe projection

This is the **security boundary** for SEC-001. Every field is 🟢. **No other fields are permitted.**

```ts
type QuestionView = {
  question_id: QuestionId;         // 🟢  e.g., "az-net-0042-fr"
  text: string;                    // 🟢  TTS-friendly (NFR-014)
  options: Array<{                 // 🟢
    key: OptionKey;                // 🟢  "A", "B", ...
    text: string;                  // 🟢
  }>;
  difficulty: "easy" | "medium" | "hard";   // 🟢
  // NO correct_answer            ← 🟡 server-only, fetched via §3.3
  // NO explanation               ← 🟡 only shown by submit_answer post-grade
  // NO tags / category            ← 🟡 irrelevant to the LLM at quiz time
  // NO score_weight               ← 🟡 grader-only
};
```

The Pydantic model that produces `QuestionView` is constructed by **explicit allowlist projection** from the AI Search record — never by `.dict()` of the full document. See §3.3 for the projection code contract.

#### 1.5.5 Fallback contract (FR-012)

If `topics.counts[requested_language] == 0`:

1. Pick the fallback language deterministically: prefer the user's previous session language, else the topic's largest-coverage language.
2. Return success with `fallback_notice` set and `language` = the resolved fallback.
3. The agent MUST relay the fallback notice to the user in their requested-but-unavailable language **before** delivering Q1 (per [004-agent-behavior §7.2](./004-agent-behavior.md)).
4. The session row records `requestedLanguage` and `language` (resolved) separately for audit.

#### 1.5.6 Idempotency contract (class `I-S`)

A second `start_quiz` call within `time_limit_seconds` of an existing `Active` session for the same `(user_id, topic)` does NOT create a second session. It returns:

```json
{ "ok": false, "error": { "code": "E_SESSION_ACTIVE", "message_user": "...", "active_session_id": "<existing-id>" } }
```

The agent must offer the user to **resume the existing session**. **There is no "force start"** in v1, **no `abandon_quiz` tool** (which would violate the five-tool allowlist in [GOV-010](./009-agent-governance.md)). The recovery path for the rare stranded-session case is the **background sweeper** (TASK-191, audit P1.8): a session left `Active` with `currentIndex == 0` and no `submit_answer` traffic for more than `voice:maxStrandedSeconds` (default 300 s) is auto-flipped to `Expired` by the sweeper, freeing the user to start fresh. The agent's localized "you have a quiz in progress on Azure Networking — resume, or wait a few minutes and try again" message names both the resume affordance and the wait-it-out path.

Preventing accidental session duplication is more important than the rare legitimate case; the sweeper closes the legitimate-case gap without adding a tool.

#### 1.5.7 Security restrictions

- `start_quiz` performs **two** AI Search queries: (a) filtered ID list for shuffle, (b) projection-only fetch of Q1 → `QuestionView`. Neither pulls `correct_answer` into application memory at this stage. Answer keys are loaded lazily per question by `submit_answer` (§3.3).
- Shuffle seed is `seed = sha256(session_id)[:16]` written to the session row before the first question is emitted (NFR-003). The seed is fully determined by `session_id` — reproducibility from `session_id` alone is required for audit replay; no server nonce is mixed in. Matches `tasks/003 TASK-049`.
- `user_id` is enforced server-side from the Entra principal; if the input's `user_id` differs, return `E_AUTH_MISMATCH`.

#### 1.5.8 Failure modes

| Code                  | Retry?     | Notes                                                            |
| --------------------- | ---------- | ---------------------------------------------------------------- |
| `E_UNKNOWN_TOPIC`     | No         | Agent should re-list topics.                                     |
| `E_INVALID_LANGUAGE`  | No         | Agent re-prompts.                                                |
| `E_INVALID_INPUT`     | No         | Validation failure; agent re-prompts.                            |
| `E_SESSION_ACTIVE`    | No         | Resume or abandon; see §1.5.6.                                   |
| `E_NO_COVERAGE`       | No         | Topic exists, but zero coverage in any allowed language.         |
| `E_SEARCH_DEGRADED`   | Yes        | Circuit-break — degrade gracefully (`session_frozen` from §1.5.9).|
| `E_BACKEND_TRANSIENT` | Yes        | SDK retry with exponential backoff (§7).                         |

#### 1.5.9 Circuit-breaker degradation

If AI Search is unavailable beyond the retry budget, return `E_SEARCH_DEGRADED` with `degrade_mode: "session_frozen"`. The agent must NOT improvise a question. (See [007-operational-runbook §3](./007-operational-runbook.md).)

#### 1.5.10 Example

```json
// Request
{
  "user_id": "8d2c9f70-9b3a-4a3e-b3e2-aa1f2b3c4d5e",
  "topic": "azure-networking",
  "n": 5,
  "language": "fr",
  "difficulty": "mixed",
  "time_limit_seconds": 600,
  "channel": "voice"
}

// Response
{
  "ok": true,
  "data": {
    "session_id": "f2c61e3a-bf85-4c1b-8f6b-1a4d0b2e9a44",
    "index": 1,
    "total": 5,
    "language": "fr",
    "time_limit_seconds": 600,
    "question_started_at": "2026-05-17T12:34:56.789Z",
    "question": {
      "question_id": "az-net-0042-fr",
      "text": "Quel service Azure fournit une connexion VPN site à site chiffrée vers un réseau virtuel ?",
      "options": [
        { "key": "A", "text": "Passerelle d'application" },
        { "key": "B", "text": "Passerelle VPN" },
        { "key": "C", "text": "Pare-feu Azure" },
        { "key": "D", "text": "Front Door" }
      ],
      "difficulty": "medium"
    }
  }
}
```

Note: no `correct_answer`, no `explanation`, no tags. That is the SEC-001 contract on the wire.

---

### 1.6 `submit_answer`

**Purpose** — Grade the user's answer deterministically (server-side), persist via a Cosmos `ifMatch` conditional write (SEC-006/NFR-002), and return the next question (or final results if last). **This is the most security-sensitive tool in the system.**

**Idempotency**: `I-K` · **Auth**: caller's Entra ID; must own `session_id` · **Hot path**: yes (voice).

#### 1.6.1 Inputs

```ts
type SubmitAnswerInput = {
  session_id: UUID;                          // 🟢
  question_id: QuestionId;                   // 🟢  must match expected current question
  raw_answer: string;                        // 🟢  user utterance, pre-normalization
  channel: "text" | "voice";                 // 🟢
  client_timestamp?: ISO8601;                // 🟢  advisory only; server enforces timing
};
```

#### 1.6.2 Validation

| Rule                                                                          | Behavior on failure         |
| ----------------------------------------------------------------------------- | --------------------------- |
| `session_id` exists, status ∈ {`Active`}                                      | `E_SESSION_NOT_ACTIVE`      |
| Session owner matches authenticated principal                                 | `E_AUTH_MISMATCH`           |
| `question_id` == `session.shuffledIds[session.currentIndex]`                  | `E_QUESTION_OUT_OF_ORDER`   |
| Per-question time budget not exceeded (compute from `session.questionStartedAt`) | Auto-grade as `unanswered`; do not error. |
| Per-quiz time budget not exceeded                                             | Flip status to `Expired`; auto-grade remainder; return `quiz_expired` result envelope. |

`E_QUESTION_OUT_OF_ORDER` is also the path taken when the agent retries a stale `submit_answer` after the next question has already been recorded. In that retry case, the tool returns `ok: true` with the **current** state (idempotent semantics) rather than the error — see §1.6.6.

#### 1.6.3 Outputs

```ts
type SubmitAnswerOutput = {
  verdict: "correct" | "incorrect" | "partial" | "unanswered";  // 🟢
  // NOTE: NO correct_answer field. Ever.                       ← 🟡 contract violation if present
  score_delta: number;                                          // 🟢  points awarded for this question
  running_score: number;                                        // 🟢
  index: number;                                                // 🟢  index of question just graded
  total: number;                                                // 🟢
  next?: QuestionView;                                          // 🟢  present iff more questions remain
  explanation?: string;                                         // 🟢  optional, language-specific; TTS-shaped
  done: boolean;                                                // 🟢  true → quiz complete
  results?: ResultsSummary;                                     // 🟢  present iff done==true; see §1.7
  question_started_at?: ISO8601;                                // 🟢  for the `next` question
};
```

The optional `explanation` is **the only field** that may carry per-question rationale into LLM context. It is sourced from the question record's `explanation` field, which is authored to be safe to share **after grading**. It MUST NOT name the correct option key directly; the author guideline is to explain the concept, not enumerate keys. (See [004-agent-behavior §9](./004-agent-behavior.md).)

#### 1.6.4 Grading algorithm (deterministic, server-side)

```
1.  Load session by id.
2.  Verify owner & status & current-question alignment.
3.  Fetch the correct_answer for question_id via QuestionSearch.get_answer_key()  ← 🟡 server-only path
4.  normalized = answer_normalizer.normalize(raw_answer, language, options)         ← see §5
5.  if normalized is None:
        verdict = "unanswered"; score_delta = 0
    elif correct_answer is a set and normalized is a set:
        if normalized == correct_answer:        verdict = "correct";  score_delta = score_weight
        elif normalized.issubset(correct_answer):
                                                verdict = "partial";  score_delta = score_weight * (|normalized| / |correct_answer|)
        else:                                   verdict = "incorrect"; score_delta = 0
    else:
        verdict = "correct" if normalized == correct_answer else "incorrect"
        score_delta = score_weight if verdict == "correct" else 0
6.  Conditional write to Cosmos (§4.4) — appends answer, advances currentIndex, updates score.
7.  Emit grading_event to App Insights (§4.5).
8.  Fetch next QuestionView if any.
9.  Return SubmitAnswerOutput.
```

Step 3 is the only place `correct_answer` enters application memory. It is held in a local variable that goes out of scope at function exit; it is never logged in cleartext, never passed to the agent, and never stored in the session row.

#### 1.6.5 Conditional-write contract (SEC-006 / NFR-002)

The persistence step uses Cosmos `ifMatch` on `_etag` keyed on the answer slot `(session_id, question_id)`:

```python
# Pseudocode for src/data/cosmos_repository.py
session = container.read_item(id=session_id, partition_key=user_id)
if any(a.question_id == question_id for a in session.answers):
    return session   # idempotent no-op: already graded
session.answers.append(new_answer)
session.score = recompute(session.score, new_answer)
session.currentIndex += 1
session.questionStartedAt = now_utc()
container.replace_item(item=session, etag=session._etag, match_condition=MatchConditions.IfNotModified)
```

A Cosmos `412 PreconditionFailed` triggers an internal retry loop: re-read, detect the now-present answer slot, return the existing graded state. **Net effect: at most one append per `(session_id, question_id)`.**

The test that locks this contract is `tests/test_idempotency.py` (TEST-007), and it must exercise the real Cosmos primitive, not a mock (see [006-testing-strategy §3](./006-testing-strategy.md)).

#### 1.6.6 Idempotency semantics

| Caller scenario                                       | Returned `verdict`                           | `running_score` mutated? |
| ----------------------------------------------------- | -------------------------------------------- | ------------------------ |
| First call for `(session, question)`                  | Computed.                                    | Yes.                     |
| Retry with same `(session, question, raw_answer)`     | Same as first call (replayed from session).  | No.                      |
| Retry with same `(session, question)` but new `raw_answer` | Same as first call. Second answer is **dropped**. | No.                      |
| Call with `question_id` < `currentIndex` (stale)       | Replay of the existing recorded verdict.    | No.                      |
| Call with `question_id` > `currentIndex`               | `E_QUESTION_OUT_OF_ORDER`.                  | No.                      |

The "second answer dropped silently" rule is intentional and the safer default: an out-of-order replay must not be able to overwrite an earlier deliberate answer. Authors can mitigate the rare "user said B then immediately corrected to C" UX in the agent layer (debounce before calling `submit_answer`).

#### 1.6.7 Security restrictions

- `correct_answer` MUST NEVER appear in the response, in any field, in any language, regardless of `verdict` value. This is asserted by `tests/test_no_answer_leakage.py` (TEST-006) across all locales.
- `explanation`, if present, MUST NOT name option keys verbatim. Authoring guideline; checked via Foundry Evaluations (NFR-010).
- The answer-key fetch path is a separate method (`QuestionSearch.get_answer_key`) on a separate code path with no agent-visible callers. See §3.3.
- `raw_answer` is logged at INFO level (it is user-supplied input), but is redacted in transcripts older than the retention window (SEC-008).

#### 1.6.8 Failure modes

| Code                       | Retry?   | Notes                                                                                                  |
| -------------------------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `E_SESSION_NOT_ACTIVE`     | No       | Session is `Paused`/`Expired`/`Completed`/`Scored`. Agent should call `get_results` or guide resumption. |
| `E_AUTH_MISMATCH`          | No       | P1 telemetry.                                                                                          |
| `E_QUESTION_OUT_OF_ORDER`  | No       | See §1.6.6 — distinguishes stale (replay) from leap-ahead (real error).                                |
| `E_CONFLICT_ETAG`          | Internal | Resolved inside the tool via re-read; never surfaces to the caller as an error.                        |
| `E_BACKEND_TRANSIENT`      | Yes      | SDK retry (§7). Idempotent by construction.                                                            |
| `E_NORMALIZER_AMBIGUOUS`   | No       | Normalizer returned multiple candidates; agent re-prompts user. See §5.5.                              |

#### 1.6.9 Example — correct answer in voice mode

```json
// Request
{
  "session_id": "f2c61e3a-bf85-4c1b-8f6b-1a4d0b2e9a44",
  "question_id": "az-net-0042-fr",
  "raw_answer": "la deuxième",
  "channel": "voice",
  "client_timestamp": "2026-05-17T12:35:08.120Z"
}

// Response
{
  "ok": true,
  "data": {
    "verdict": "correct",
    "score_delta": 1.0,
    "running_score": 1.0,
    "index": 1,
    "total": 5,
    "done": false,
    "question_started_at": "2026-05-17T12:35:09.350Z",
    "next": {
      "question_id": "az-net-0010-fr",
      "text": "Quelle plage d'adresses est réservée aux sous-réseaux privés selon la RFC 1918 ?",
      "options": [
        { "key": "A", "text": "10.0.0.0/8" },
        { "key": "B", "text": "192.0.2.0/24" },
        { "key": "C", "text": "169.254.0.0/16" },
        { "key": "D", "text": "224.0.0.0/4" }
      ],
      "difficulty": "easy"
    }
  }
}
```

Note: no `correct_answer`. The verdict is `correct`, period. SEC-001 enforced.

#### 1.6.10 Example — duplicate submission (retry)

```json
// First call returned verdict "correct"; agent retries after WS hiccup
// Second call: identical request body as above

// Response (replayed from session; no double-score)
{
  "ok": true,
  "data": {
    "verdict": "correct",
    "score_delta": 1.0,
    "running_score": 1.0,    // ← unchanged: idempotent
    "index": 1,
    "total": 5,
    "done": false,
    "next": { "...": "..." }
  }
}
```

#### 1.6.11 Example — final question, quiz complete

```json
// Response on the 5th submission
{
  "ok": true,
  "data": {
    "verdict": "correct",
    "score_delta": 1.0,
    "running_score": 4.0,
    "index": 5,
    "total": 5,
    "done": true,
    "results": {
      "session_id": "f2c61e3a-bf85-4c1b-8f6b-1a4d0b2e9a44",
      "score": 4.0,
      "max_score": 5.0,
      "percentage": 80.0,
      "pass": true,
      "pass_threshold_pct": 60.0,
      "language": "fr",
      "duration_seconds": 412,
      "breakdown": [
        { "question_id": "az-net-0042-fr", "verdict": "correct",   "score": 1.0 },
        { "question_id": "az-net-0010-fr", "verdict": "correct",   "score": 1.0 },
        { "question_id": "az-net-0027-fr", "verdict": "incorrect", "score": 0.0 },
        { "question_id": "az-net-0033-fr", "verdict": "correct",   "score": 1.0 },
        { "question_id": "az-net-0051-fr", "verdict": "correct",   "score": 1.0 }
      ]
    }
  }
}
```

Note: `breakdown` carries verdicts but **never** the expected answer keys (SEC-001).

---

### 1.7 `get_results`

**Purpose** — Compute and return the final summary for a completed (or expired) session, in the session's language.

**Idempotency**: `R` · **Auth**: session owner · **Hot path**: yes (end of quiz).

#### Inputs

```ts
type GetResultsInput = {
  session_id: UUID;                  // 🟢
  user_id: UUID;                     // 🟢  authenticated principal
};
```

#### Validation

| Rule                              | Behavior on failure       |
| --------------------------------- | ------------------------- |
| Session exists                    | `E_SESSION_NOT_FOUND`     |
| Owner matches authenticated user  | `E_AUTH_MISMATCH`         |
| Status ∈ {`Completed`, `Scored`, `Expired`} | Allowed.        |
| Status == `Active` or `Paused`    | `E_SESSION_NOT_FINAL` — call returns the **in-progress** summary, marked partial. |

#### Outputs

```ts
type GetResultsOutput = ResultsSummary;

type ResultsSummary = {
  session_id: UUID;                  // 🟢
  status: SessionStatus;             // 🟢
  score: number;                     // 🟢
  max_score: number;                 // 🟢
  percentage: number;                // 🟢  0..100
  pass: boolean;                     // 🟢
  pass_threshold_pct: number;        // 🟢
  language: ISO639-1;                // 🟢
  duration_seconds: number;          // 🟢
  breakdown: Array<{                 // 🟢
    question_id: QuestionId;
    verdict: "correct" | "incorrect" | "partial" | "unanswered";
    score: number;
    // NO expected_answer            ← 🟡 server-only
    // NO option_correctness         ← 🟡 do not return per-option correctness array
  }>;
};
```

#### Security restrictions

- Same SEC-001 boundary as `submit_answer`: the breakdown carries verdicts, not keys.
- `get_results` does NOT call AI Search for answer keys — everything in the breakdown comes from `sessions.answers`, which never persisted the key in the first place.

#### Failure modes

| Code                  | Retry? | Notes                                              |
| --------------------- | ------ | -------------------------------------------------- |
| `E_SESSION_NOT_FOUND` | No     | Agent informs user gracefully.                     |
| `E_AUTH_MISMATCH`     | No     | P1 telemetry.                                      |
| `E_SESSION_NOT_FINAL` | No     | Agent decides whether to surface partial summary.  |
| `E_BACKEND_TRANSIENT` | Yes    | SDK retry (§7).                                    |

---

## 2. Cosmos DB Document Schemas

Four containers as described in [003-data-contracts §4](./003-data-contracts.md). This section is the **wire-level** schema with validation rules and indexing notes.

### 2.1 `sessions` container

- **Partition key**: `/userId`  (NFR-005)
- **TTL**: enabled; default `null` (no expire); set to `2592000` (30 days) on transition to `Scored`/`Expired` so stale sessions self-clean. Matches `infra/README.md §12.1` and `tasks/003 TASK-050`.
- **Conditional writes**: required on every update (SEC-006). Reads use `read_item` with partition key.
- **Indexing**: include `status`, `topic`, `language`, `startedAt`. Exclude `shuffledIds` (large; not queried).

#### Document schema

```ts
type SessionDoc = {
  id: UUID;                                      // 🟢  == sessionId
  userId: UUID;                                  // 🟢  partition key
  topic: LogicalId;                              // 🟢
  language: ISO639-1;                            // 🟢  resolved language (may differ from requestedLanguage)
  requestedLanguage: ISO639-1;                   // 🟢  what the user asked for
  seed: string;                                  // 🟡  shuffle seed (16 hex chars); not returned to LLM
  shuffledIds: QuestionId[];                     // 🟡  full ordered list; SERVER-only
  currentIndex: number;                          // 🟢  next-question index, 0-based
  answers: Array<{                               // 🟢  ordered, append-only
    question_id: QuestionId;                     // 🟢
    received_raw: string;                        // 🟢  raw user utterance (PII; see SEC-008)
    received_normalized: string | string[] | null; // 🟢  normalizer output
    verdict: "correct" | "incorrect" | "partial" | "unanswered";   // 🟢
    score_delta: number;                         // 🟢
    answered_at: ISO8601;                        // 🟢
    channel: "text" | "voice";                   // 🟢
    latency_ms: number;                          // 🟢
    // NO expected                               ← 🟡 NEVER persisted; fetch on demand
  }>;
  score: number;                                 // 🟢
  maxScore: number;                              // 🟢
  status: SessionStatus;                         // 🟢
  startedAt: ISO8601;                            // 🟢
  questionStartedAt: ISO8601;                    // 🟢
  timeLimitSeconds: number;                      // 🟢
  perQuestionLimitSeconds: number;               // 🟢  (matches tasks/005 TASK-090)
  passThresholdPct: number;                      // 🟢  default 60
  channel: "text" | "voice";                     // 🟢  most-recent channel
  _etag: Etag;                                   // 🔴  system field; concurrency token
  _ts: number;                                   // (system)
  ttl: number | null;                            // 🟢
};

type SessionStatus =
  | "Active"      // accepting submit_answer
  | "Paused"      // user disconnected; agent can resume
  | "Expired"     // timer ran out; auto-graded
  | "Completed"   // last question answered, not yet finalized
  | "Scored";     // get_results computed; terminal
```

#### Why `expected` is never persisted

If the answer key were stored in the session row, a Cosmos read by a misconfigured tool — or a future log-export pipeline — could leak the entire answer bank by replaying graded sessions. By storing only `verdict` + `received_*`, the session row is **safe to export** for analytics without filtering.

#### Validation rules

| Rule                                                                  | Where enforced                |
| --------------------------------------------------------------------- | ----------------------------- |
| `language` ∈ allowlist (SEC-010)                                      | Pydantic model + tool layer.  |
| `currentIndex ∈ [0, len(shuffledIds)]`                                | Tool layer pre-write.         |
| `len(answers) == currentIndex` after each successful write            | Tool layer pre-write.         |
| `status` transitions follow §4.3                                      | Tool layer pre-write.         |
| `_etag` matches on conditional update                                 | Cosmos `ifMatch`.             |
| `answers[i].question_id == shuffledIds[i]`                            | Tool layer pre-write.         |

#### Example

```json
{
  "id": "f2c61e3a-bf85-4c1b-8f6b-1a4d0b2e9a44",
  "userId": "8d2c9f70-9b3a-4a3e-b3e2-aa1f2b3c4d5e",
  "topic": "azure-networking",
  "language": "fr",
  "requestedLanguage": "fr",
  "seed": "3f1e9a7c4b2d8e60",
  "shuffledIds": ["az-net-0042-fr", "az-net-0010-fr", "az-net-0027-fr", "az-net-0033-fr", "az-net-0051-fr"],
  "currentIndex": 2,
  "answers": [
    {
      "question_id": "az-net-0042-fr",
      "received_raw": "la deuxième",
      "received_normalized": "B",
      "verdict": "correct",
      "score_delta": 1.0,
      "answered_at": "2026-05-17T12:35:08.250Z",
      "channel": "voice",
      "latency_ms": 142
    },
    {
      "question_id": "az-net-0010-fr",
      "received_raw": "10.0.0.0/8",
      "received_normalized": "A",
      "verdict": "correct",
      "score_delta": 1.0,
      "answered_at": "2026-05-17T12:35:31.880Z",
      "channel": "voice",
      "latency_ms": 138
    }
  ],
  "score": 2.0,
  "maxScore": 5.0,
  "status": "Active",
  "startedAt": "2026-05-17T12:34:56.789Z",
  "questionStartedAt": "2026-05-17T12:35:33.100Z",
  "timeLimitSeconds": 600,
  "perQuestionLimitSeconds": 60,
  "passThresholdPct": 60.0,
  "channel": "voice",
  "_etag": "\"00000000-0000-0000-fe9d-2ad6a08e01dc\"",
  "_ts": 1779381333,
  "ttl": null
}
```

### 2.2 `users` container

- **Partition key**: `/userId`
- **TTL**: not enabled.

```ts
type UserDoc = {
  id: UUID;                          // 🟢  == userId
  userId: UUID;                      // 🟢  partition key
  language: ISO639-1;                // 🟢
  detectedLanguage?: ISO639-1;       // 🟢  the model's first-message detection
  explicitlySet: boolean;            // 🟢  true iff via set_language
  createdAt: ISO8601;                // 🟢
  updatedAt: ISO8601;                // 🟢
  _etag: Etag;                       // 🔴
};
```

Validation: `language` in allowlist; `detectedLanguage` may be any ISO 639-1 (we record what was detected even if not supported, for the fallback decision in §1.5.5).

### 2.3 `topics` container

- **Partition key**: `/topicId`  (small catalog — single logical partition is also fine; the choice is local to the deploy).
- **TTL**: not enabled. Cached in App Configuration with polling reload (NFR-014).

```ts
type TopicDoc = {
  id: LogicalId;                                              // 🟢  == topicId
  topicId: LogicalId;                                         // 🟢
  labels: Record<ISO639-1, string>;                           // 🟢
  counts: Record<ISO639-1, number>;                           // 🟢
  defaultLanguage: ISO639-1;                                  // 🟢  for fallback
  enabled: boolean;                                           // 🟢
  updatedAt: ISO8601;                                         // 🟢
};
```

### 2.4 `audit` container

- **Partition key**: `/sessionId`  (separate from `sessions` so retention/export differ).
- **TTL (hot)**: 365 days default in Cosmos (compliance/disputes window); configurable. **A pre-TTL job archives each row to immutable Blob storage** so the full audit trail is retained for **7 years** (per `infra/README.md §12.1`) without paying Cosmos cost for cold data.
- **Append-only** by convention; one document per `grading_event`.

```ts
type AuditEvent = {
  id: UUID;                                                   // 🟢  event id
  sessionId: UUID;                                            // 🟢  partition key
  userId: UUID;                                               // 🟢
  questionId: QuestionId;                                     // 🟢
  language: ISO639-1;                                         // 🟢
  channel: "text" | "voice";                                  // 🟢
  expected: OptionKey[] | string[];                           // 🟡  ALLOWED here (audit is server-only) — see note below
  received: string;                                           // 🟢  normalized form
  receivedRaw: string;                                        // 🟢  PII; retention applies
  verdict: "correct" | "incorrect" | "partial" | "unanswered"; // 🟢
  scoreDelta: number;                                         // 🟢
  latencyMs: number;                                          // 🟢
  timestamp: ISO8601;                                         // 🟢
};
```

**`expected` in audit — a deliberate exception**: the audit log records what the answer key was at the time of grading so disputes are resolvable years later, even after the question bank is edited. This is server-only data: the `audit` container is accessed by no tool that returns to the agent, and RBAC restricts it to an analyst/auditor role separate from the agent identity. The `correct_answer`-never-to-LLM rule (SEC-001) is preserved because the LLM never reads `audit`.

---

## 3. AI Search Index Schema

### 3.1 Index name & versioning

- Index name pattern: `questions-v{N}` (e.g., `questions-v1`).
- Reindexes are blue/green: new version built and validated under a side index, then aliased to the production name (alias support in AI Search).

### 3.2 Field definitions

| Field            | Type             | Searchable | Filterable | Facetable | Retrievable | Analyzer                  | Sensitivity |
| ---------------- | ---------------- | ---------- | ---------- | --------- | ----------- | ------------------------- | ----------- |
| `id`             | `Edm.String` PK  |            | ✓          |           | ✓           | `keyword`                 | 🟢          |
| `logical_id`     | `Edm.String`     |            | ✓          | ✓         | ✓           | `keyword`                 | 🟢          |
| `topic`          | `Edm.String`     |            | ✓          | ✓         | ✓           | `keyword`                 | 🟢          |
| `language`       | `Edm.String`     |            | ✓ **(required filter)** | ✓ | ✓ | `keyword`                 | 🟢          |
| `text`           | `Edm.String`     | ✓          |            |           | ✓           | per-record: `en.microsoft` / `fr.microsoft` / `es.microsoft` | 🟢 |
| `options`        | `Collection(Edm.ComplexType)` |   |            |           | ✓           | child fields use the record's language analyzer | 🟢 |
| `options.key`    | `Edm.String`     |            |            |           | ✓           | `keyword`                 | 🟢          |
| `options.text`   | `Edm.String`     | ✓          |            |           | ✓           | same as `text`            | 🟢          |
| `correct_answer` | `Collection(Edm.String)` |    | ✗          | ✗         | ✓ **(restricted)** | `keyword`         | 🟡 **SERVER-ONLY** |
| `difficulty`     | `Edm.String`     |            | ✓          | ✓         | ✓           | `keyword`                 | 🟢          |
| `tags`           | `Collection(Edm.String)` |    | ✓          | ✓         | ✓           | `keyword`                 | 🟡          |
| `category`       | `Edm.String`     |            | ✓          | ✓         | ✓           | `keyword`                 | 🟡          |
| `explanation`    | `Edm.String`     | ✓          |            |           | ✓           | per-record language analyzer | 🟢       |
| `score_weight`   | `Edm.Double`     |            | ✓          |           | ✓           | —                         | 🟡          |

#### Notes

- `language` is **always** filtered. No query path ever omits it.
- `correct_answer` is `Retrievable: true` but accessed only by a single repository method (§3.3) under a server-only RBAC role; the agent's tool path uses a separate field-projection query that excludes it.
- One record per `(logical_id, language)` pair (NFR-011).

### 3.3 Projection contracts (the SEC-001 enforcement point)

Two distinct repository methods, in `src/data/question_search.py`:

#### 3.3.1 `get_question_view(question_id) -> QuestionView`

```python
# Pseudocode — the LLM-safe path.
# Two-layer allowlist: SEARCH_FIELDS for the AI Search projection,
# then explicit field-by-field construction of QuestionView so the
# Pydantic model's field set (§1.5.4) is the load-bearing boundary,
# not the search projection.
SEARCH_FIELDS = ["id", "text", "options", "difficulty"]
# explicit allowlist; NOT a denylist. The literal "correct_answer" string
# does not appear in this method.

def get_question_view(self, question_id: str) -> QuestionView:
    doc = self._search.get_document(
        key=question_id,
        selected_fields=SEARCH_FIELDS,
    )
    return QuestionView(
        question_id=doc["id"],
        text=doc["text"],
        options=doc["options"],
        difficulty=doc["difficulty"],
    )
```

The `selected_fields` argument is passed to AI Search's REST API as the `$select` projection. The result document literally does not contain `correct_answer`. Even a logging mistake (`log.info(doc)`) cannot leak it. The explicit `QuestionView(...)` construction is the second layer — even if a future maintainer widens `SEARCH_FIELDS`, the Pydantic model (with `extra="forbid"` per §1.5.4) rejects the additional fields. `logical_id`, `topic`, `language` are intentionally NOT in `QuestionView` — they belong on the parent `StartQuizOutput` envelope (§1.5.3), not on the per-question view.

#### 3.3.2 `get_answer_key(question_id) -> AnswerKey`

```python
# Pseudocode — the server-only path. NOT called from start_quiz or anywhere
# that returns to the agent.
def get_answer_key(self, question_id: str) -> AnswerKey:
    doc = self._search.get_document(
        key=question_id,
        selected_fields=["id", "correct_answer", "score_weight"],
    )
    return AnswerKey(question_id=doc["id"],
                     correct=set(doc["correct_answer"]),
                     score_weight=doc["score_weight"])
```

Only `submit_answer` calls `get_answer_key`. The result type (`AnswerKey`) has no serializer that would emit it into a tool response; the type-system itself is part of the boundary.

### 3.4 Query patterns

#### Filtered ID draw (called by `start_quiz`)

```
GET /indexes/questions-v1/docs?
  search=*&
  $filter=topic eq 'azure-networking' and language eq 'fr' and
         (difficulty eq 'easy' or difficulty eq 'medium' or difficulty eq 'hard')&
  $select=id,logical_id,difficulty,score_weight&
  $top=200&
  searchMode=any
```

The application then performs the seeded shuffle on the returned ID list (NFR-003); AI Search never sees the seed.

#### Single-question view (called by `start_quiz`, `submit_answer.next`)

`GET /indexes/questions-v1/docs/{question_id}?$select=id,logical_id,topic,language,text,options,difficulty`

#### Answer-key lookup (server-only, `submit_answer`)

`GET /indexes/questions-v1/docs/{question_id}?$select=id,correct_answer,score_weight`

This call is made under the agent's Managed Identity (which has `Search Index Data Reader`, SEC-005). The boundary is **not** RBAC — it is the projection. RBAC is the second line of defense; the projection is the first.

### 3.5 Synonym maps

Per-language synonym maps for topic aliases (e.g., FR: "réseau" ↔ "réseaux", ES: "redes" ↔ "red"). Maps live in App Configuration and are applied at index time.

---

## 4. Cross-cutting Contracts

### 4.1 Validation rules summary

Tabular form (the exhaustive matrix; per-tool failure mode tables in §1 reference these):

| Field                  | Rule                                                                       |
| ---------------------- | -------------------------------------------------------------------------- |
| `language`             | ISO 639-1, in App Configuration allowlist (SEC-010).                       |
| `user_id`              | UUID v4; must match authenticated Entra subject.                           |
| `session_id`           | UUID v4; must exist; owner must match.                                     |
| `question_id`          | Matches `^[a-z0-9-]+-{lang}$`; lang must equal session language.           |
| `topic`                | `LogicalId`; must exist in `topics` container with `enabled == true`.     |
| `n` (start_quiz)       | Integer in `[1, 50]`; clamped to `topics.counts[language]`.                |
| `time_limit_seconds`   | Integer in `[60, 3600]`.                                                   |
| `raw_answer`           | Length `[1, 512]`. Non-empty after trim. UTF-8.                            |
| `OptionKey`            | Single `[A-Z]`; in question's option set.                                  |
| `channel`              | `"text" \| "voice"`.                                                        |
| `difficulty`           | `"easy" \| "medium" \| "hard" \| "mixed"`.                                   |

### 4.2 Error contract (envelope)

#### 4.2.1 Envelope shape

```ts
type ToolError = {
  ok: false;
  error: {
    code: ErrorCode;            // 🟢  stable enum (§6.2)
    message_user: string;       // 🟢  localized, TTS-friendly, safe to read aloud — the ONLY string the renderer surfaces to LLM context (§6.4)
    message_dev?: string;       // 🟡  developer diagnostic; not shown to LLM; correlated via correlation_id to App Insights
    correlation_id?: string;    // 🟢  W3C traceparent-derived ID for support correlation (was previously named `trace_id`; renamed for OTel alignment — same value)
    retryable: boolean;         // 🟢
    retry_after_ms?: number;    // 🟢
    detail?: Record<string, unknown>;  // 🟡  e.g., { "active_session_id": "..." } — server-only
  };
};
```

`message_user` is the **only** string permitted to enter LLM context from an error. It is localized to the session language (or the user's preferred language if no session yet), and authored to be TTS-shaped (NFR-014).

#### 4.2.2 Error code enum

| Code                       | Class    | Retryable | Surfaces to user? |
| -------------------------- | -------- | --------- | ----------------- |
| `E_INVALID_INPUT`          | 400      | No        | Yes               |
| `E_INVALID_LANGUAGE`       | 400      | No        | Yes               |
| `E_UNKNOWN_TOPIC`          | 400      | No        | Yes               |
| `E_NO_COVERAGE`            | 400      | No        | Yes               |
| `E_AUTH`                   | 401      | No        | Telemetry only    |
| `E_AUTH_MISMATCH`          | 403      | No        | Telemetry only    |
| `E_SESSION_NOT_FOUND`      | 404      | No        | Yes               |
| `E_SESSION_NOT_ACTIVE`     | 409      | No        | Yes               |
| `E_SESSION_NOT_FINAL`      | 409      | No        | Yes (partial)     |
| `E_SESSION_ACTIVE`         | 409      | No        | Yes (offers resume) |
| `E_QUESTION_OUT_OF_ORDER`  | 409      | No        | Telemetry only    |
| `E_CONFLICT_ETAG`          | 412      | Internal  | Never surfaces    |
| `E_NORMALIZER_AMBIGUOUS`   | 422      | No        | Yes (re-prompt)   |
| `E_BACKEND_TRANSIENT`      | 503      | Yes       | Soft notice       |
| `E_SEARCH_DEGRADED`        | 503      | Yes       | Soft notice       |
| `E_RATE_LIMITED`           | 429      | Yes       | Yes (after backoff) |
| `E_INTERNAL`               | 500      | No        | Soft notice       |

### 4.3 Session state machine

The lifecycle from [002-system-architecture §5](./002-system-architecture.md) formalized as a transition contract. Each transition is annotated with: trigger, who performs it, and the conditional-write guard.

```mermaid
stateDiagram-v2
  [*] --> Active : start_quiz (Cosmos insert)
  Active --> Active : submit_answer (Cosmos ifMatch)
  Active --> Paused : heartbeat absent > sessions:pauseThresholdSeconds (AppConfig, default 300s)
  Paused --> Active : submit_answer or get_results
  Active --> Expired : timer elapsed; auto-grade remainder
  Paused --> Expired : timer elapsed
  Active --> Completed : last answer accepted
  Expired --> Scored : get_results
  Completed --> Scored : get_results
  Scored --> [*] : TTL-driven cleanup (per ADR 006 — 30d default)
```

| Transition            | Trigger                          | Performed by         | Guard (Cosmos)                                                |
| --------------------- | -------------------------------- | -------------------- | ------------------------------------------------------------- |
| `[*]` → `Active`      | `start_quiz` success             | `start_quiz` tool    | Insert; `id` unique.                                          |
| `Active` → `Active`   | `submit_answer` (not last)       | `submit_answer` tool | `ifMatch(_etag)`; append-only on `answers`; `currentIndex++`. |
| `Active` → `Paused`   | Inactivity heartbeat job         | Background sweeper   | `ifMatch(_etag)`; status only.                                |
| `Paused` → `Active`   | Any tool call with `session_id`  | Any tool             | `ifMatch(_etag)`; status only.                                |
| `Active`/`Paused` → `Expired` | Per-quiz timer elapsed   | Background sweeper or tool | `ifMatch(_etag)`; auto-grade remaining as `unanswered`. |
| `Active` → `Completed`| Last answer accepted             | `submit_answer` tool | `ifMatch(_etag)`; `currentIndex == len(shuffledIds)`.          |
| `Completed`/`Expired` → `Scored` | `get_results`         | `get_results` tool   | `ifMatch(_etag)`; set per-item `ttl` per ADR 006 (30 days hot).|
| `Scored` → `[*]`      | TTL                              | Cosmos               | —                                                              |

#### 4.3.1 Forbidden transitions

| From       | To         | Reason                                                                 |
| ---------- | ---------- | ---------------------------------------------------------------------- |
| `Scored`   | `Active`   | Terminal; would defeat audit. To retake, create a new session.         |
| `Expired`  | `Active`   | Same as above.                                                         |
| `Completed`| `Active`   | Same.                                                                  |
| `*`        | `*` (same) | No-op transitions are rejected to keep the audit trail clean.          |

### 4.4 Idempotency rules

Already detailed per-tool; summarized:

| Rule | Where it lives                                                              |
| ---- | --------------------------------------------------------------------------- |
| Cosmos `ifMatch` on every `sessions` update.                                | `src/data/cosmos_repository.py`        |
| Append-only `answers[]` with `(session_id, question_id)` uniqueness guard.  | `submit_answer` pre-write check.       |
| `start_quiz` idempotency via active-session lookup (`I-S`).                 | `start_quiz` pre-insert query.         |
| Tool retries by the SDK on `E_BACKEND_TRANSIENT` are safe by construction.  | All tools — guaranteed by the above.   |

### 4.5 Grading event contract (`grading_event`)

Emitted by `submit_answer` after a successful conditional write. **The two sinks carry different shapes**: the App Insights event is the broad-access telemetry stream; the Cosmos `audit` row is the server-only, RBAC-restricted system of record.

#### 4.5.1 App Insights `grading_event` (broad-access telemetry — must NOT contain answer-keys or raw PII)

```ts
type GradingEventTelemetry = {
  // App Insights custom event name: "grading_event"
  sessionId: UUID;
  questionId: QuestionId;
  userId: UUID;                    // 🟢  opaque Entra OID
  language: ISO639-1;
  channel: "text" | "voice";
  // NO `expected`                  ← 🟡 server-only; would expose the answer key to a broader-access surface than the audit container. Persist only in `audit` (§2.4).
  received: string;                // 🟢  normalized option key (e.g., "B"), NOT free text
  // NO `receivedRaw`               ← 🟢 PII; transcript retention applies separately. Persist only in `audit` (§2.4) and Realtime transcripts.
  verdict: "correct" | "incorrect" | "partial" | "unanswered";
  scoreDelta: number;
  latencyMs: number;
  timestamp: ISO8601;
};
```

#### 4.5.2 Cosmos `audit` row (server-only, RBAC-restricted — see §2.4)

Schema is defined in [§2.4 `AuditEvent`](#24-audit-container). It **does** include `expected` (🟡, allowed here because the `audit` container is the system of record for dispute resolution and is accessed only by analyst/auditor roles) and `receivedRaw` (PII, subject to retention).

#### 4.5.3 Rationale

The cleanest expression of SEC-001 in telemetry is: **answer keys never leave the server-only data tier**. The `audit` Cosmos container is server-only by RBAC; App Insights and Log Analytics are accessed by a broader engineering audience under different retention. Emitting `expected` to App Insights would widen the trust boundary of the answer keys without an audit-grade benefit. The grading-correctness dashboard ([007-operational-runbook §2.2](./007-operational-runbook.md)) joins `audit` (for `expected`) with `grading_event` (for everything else) at query time when needed.

TEST-010 asserts emission with all telemetry dimensions present **and** asserts `expected` and `receivedRaw` are **absent** from the App Insights event. Matches `infra/README §11.2` (INF-101: forbidden log content) and the test plan's AL-006.

### 4.6 Retry contract

| Surface                | Failures retried             | Strategy                                                     | Cap          |
| ---------------------- | ---------------------------- | ------------------------------------------------------------ | ------------ |
| Cosmos calls           | 429, 503, network timeouts   | Exponential backoff (50 ms, 100 ms, 200 ms, 400 ms, 800 ms)  | 5 attempts   |
| AI Search calls        | 503, network timeouts        | Exponential backoff (100 ms, 250 ms, 500 ms, 1 s)            | 4 attempts   |
| Cosmos `412` (etag)    | Always — concurrency norm    | Re-read + re-apply or detect already-applied                 | 3 attempts then `E_INTERNAL` |
| **Tool-call retry delay** (agent → tool) | one retry         | Wait 500 ms (text) / 250 ms (voice) before retrying          | 1 retry      |
| **Tool-call timeout** (agent → tool) — abandons the call | — | Per GOV-014: 2s (text) / 800 ms (voice). On expiry: apply GOV-013 ambiguous-failure rule (call `get_results` to check post-state, then advance). | — |
| Agent-level retries on `submit_answer` | not required by the SDK | Idempotent (`I-K`) so safe regardless                | —            |

Retries respect `retry_after_ms` when set on `E_RATE_LIMITED` or `E_BACKEND_TRANSIENT`.

### 4.7 Timing & timer contract (NFR-004)

- `session.startedAt`, `session.questionStartedAt`, `session.timeLimitSeconds`, `session.perQuestionLimitSeconds` are the **only** authoritative timing fields. The client/agent is never trusted to enforce time.
- On every `submit_answer`, the tool checks both timers before grading. If exceeded:
  - Per-question only → grade current as `unanswered`; advance.
  - Per-quiz exceeded → flip to `Expired`, auto-grade remainder as `unanswered`, return `done: true` with results envelope.
- A background sweeper job (Functions or scheduled job) flips silently-abandoned `Active` sessions to `Expired` once the quiz timer elapses, even with no `submit_answer` traffic.

---

## 5. Voice Normalization Contract

The `answer_normalizer` ([004-agent-behavior §6](./004-agent-behavior.md)) converts spoken/typed user input into a deterministic comparable value before grading. It is multilingual, language-aware, and lives in `src/agent/answer_normalizer.py`.

### 5.1 Signature

```python
class AnswerNormalizer:
    def normalize(
        self,
        raw_answer: str,
        language: str,                          # ISO 639-1
        options: list[QuestionOption],          # the question's options
        accept_multi: bool = False,             # multi-correct questions
    ) -> NormalizeResult: ...

@dataclass
class NormalizeResult:
    matched: list[str] | None        # list of OptionKey; None if no match OR explicit skip
    confidence: float                # 0..1
    strategy: str                    # which matcher won: "key" | "ordinal" | "option_text" | "fuzzy" | "negation_reject" | "skip"
    ambiguous: bool                  # true → caller should re-prompt
```

### 5.2 Matching strategies (in order)

1. **`key`** — direct letter: `"A"`, `"option a"`, `"letter A"`, FR `"option A"`, ES `"opción A"`.
2. **`ordinal`** — "the first", "the second", FR `"la première"`, `"la deuxième"`, ES `"la primera"`, `"la segunda"`. Bounded by `len(options)`.
3. **`option_text`** — exact or near-exact match (Levenshtein distance ≤ 2 on lowered-normalized text) to one option's `text`.
4. **`fuzzy`** — language-aware tokenization + token-set ratio; threshold 0.85.
5. **`negation_reject`** — detect "not A", "anything but B", `"sauf C"`, `"excepto D"` → if reading would otherwise yield A/B/C/D, return `matched=None, strategy=negation_reject`. The agent re-prompts; we never grade a negated answer.
6. **`skip`** — explicit skip intent: EN `"skip"`, `"pass"`; FR `"passer"`, `"je passe"`; ES `"saltar"`, `"paso"`. Returns `matched=None, strategy=skip`. Distinct from `negation_reject` (no re-prompt — grader records `verdict="unanswered"` per [GOV-104](./009-agent-governance.md)).

If two strategies yield different non-equal results, `ambiguous=True` and `matched=None`; the tool returns `E_NORMALIZER_AMBIGUOUS` and the agent re-prompts ("I didn't quite catch that — was it A or B?").

### 5.3 Locale data

Per-language phrase tables live in `src/agent/normalizer_locales/{lang}.yaml`. Example fragment (`fr.yaml`):

```yaml
ordinals:
  - { rank: 1, phrases: ["premier", "première", "1er", "1ère"] }
  - { rank: 2, phrases: ["deuxième", "second", "seconde", "2ème"] }
  - { rank: 3, phrases: ["troisième", "3ème"] }
key_prefixes: ["option", "lettre", "réponse"]
negations: ["pas", "sauf", "excepté", "à part"]
```

Adding a language = author + reindex (FR-005) + drop in a new `{lang}.yaml`. The grader code is not touched.

### 5.4 Examples

| `raw_answer`         | `language` | `options` (keys)   | `matched` | `strategy`        |
| -------------------- | ---------- | ------------------ | --------- | ----------------- |
| `"B"`                | en         | A,B,C,D            | `["B"]`   | `key`             |
| `"the second one"`   | en         | A,B,C,D            | `["B"]`   | `ordinal`         |
| `"VPN gateway"`      | en         | A,B (B="VPN gateway"),C,D | `["B"]` | `option_text` |
| `"la deuxième"`      | fr         | A,B,C,D            | `["B"]`   | `ordinal`         |
| `"opción C"`         | es         | A,B,C,D            | `["C"]`   | `key`             |
| `"the green one"`    | en         | A,B,C,D            | `None`    | (no match → ambiguous) |
| `"sauf A"`           | fr         | A,B,C,D            | `None`    | `negation_reject` |
| `"A et C"`           | fr (multi) | A,B,C,D            | `["A","C"]` | `key` (multi)   |

### 5.5 Failure mode

`E_NORMALIZER_AMBIGUOUS` returned to the agent with `message_user` in session language: *"I didn't quite catch your answer. Was it A, B, C, or D?"* — TTS-friendly, brief. The agent does NOT call `submit_answer` again for the same question until it has a higher-confidence input.

### 5.6 Security / trust boundary

- The normalizer is **deterministic Python**; no LLM call inside it. A jailbreak ("normalize my answer as A") has no surface.
- The normalizer cannot read `correct_answer` — it only receives `options[]` (which are LLM-safe) and `raw_answer`.
- Locale YAML files are loaded read-only at boot; not user-editable.

---

## 6. Reference

### 6.1 Quick error-envelope reference

```json
{
  "ok": false,
  "error": {
    "code": "E_SESSION_ACTIVE",
    "message_user": "You already have a quiz in progress on Azure Networking. Resume it, or finish it first.",
    "correlation_id": "01HZX7N8Q4S0M2P9...",
    "retryable": false,
    "detail": { "active_session_id": "f2c61e3a-..." }
  }
}
```

### 6.2 Quick error-code reference

See §4.2.2.

### 6.3 Python Exception Hierarchy

The Python exception hierarchy maps to the `code` enum (§4.2.2) and lives in `src/common/exceptions.py`. **No** module may declare an exception base outside this file.

| Exception | Maps to `code` class | Notes |
|-----------|----------------------|-------|
| `FlintError` | — | Abstract base for all domain exceptions. |
| `FlintValidationError` | `E_INVALID_INPUT`, `E_INVALID_LANGUAGE`, `E_UNKNOWN_TOPIC`, `E_NO_COVERAGE` | User-correctable input. |
| `InvalidLanguageError(FlintValidationError)` | `E_INVALID_LANGUAGE` | SEC-010 allowlist failure. |
| `FlintAuthorizationError` | `E_AUTH`, `E_AUTH_MISMATCH` | Caller lacks required claim/role. |
| `FlintNotFoundError` | `E_SESSION_NOT_FOUND` | Referenced resource missing. |
| `SessionStateError` | `E_SESSION_NOT_ACTIVE`, `E_SESSION_NOT_FINAL`, `E_SESSION_ACTIVE`, `E_QUESTION_OUT_OF_ORDER` | Forbidden transition per §4.3. |
| `FlintConflictError` | `E_CONFLICT_ETAG` (internal — never surfaces) | Cosmos 412; retried internally. |
| `FlintUpstreamError` | `E_BACKEND_TRANSIENT`, `E_SEARCH_DEGRADED`, `E_RATE_LIMITED`, `E_INTERNAL` | Downstream Azure failure. |
| `FlintConfigurationError` | (startup) | Misconfiguration; fail loud on boot, never at request time. |
| `AnswerLeakageError` | (P0; halts session) | 🔴 a 🟡 field was about to cross the LLM boundary. Always pages on-call. |

Tool functions translate these to the `ToolError` envelope (§4.2.1). Internal codes and stack traces never reach the LLM. Tests parametrize across exception types and assert correct envelope mapping.

### 6.4 Error Envelope Rendering Layer

This is the **single point of SEC-001 enforcement on error paths**. Lives in `src/agent/renderer.py`.

**Contract**:

1. Every `ToolError` returned from a tool MUST pass through `Renderer.render_error(envelope) -> str` before any string enters LLM context.
2. The renderer surfaces **only** `error.message_user`. Every other field — `code`, `message_dev`, `correlation_id`, `retryable`, `retry_after_ms`, `detail` — is dropped from the LLM-visible string. Those fields go to App Insights via the correlation_id join key (§4.5).
3. **Forbidden patterns** (lint rule `LOG001`):
   - `f"Tool error: {error}"` — interpolates the full envelope.
   - `logger.info("error", extra={"envelope": envelope.model_dump()})` — dumps 🟡 `detail`/`message_dev` to logs.
   - Any string concatenation involving `envelope.detail` or `envelope.message_dev` that flows back to the agent.
4. **Required emission**: every rendered error produces one `agent.tool_error` structured event with `correlation_id`, `code`, `tool_name`, and `class` (mapped from the exception type per §6.3.1). The `message_dev` and `detail` fields are written **only** to App Insights customDimensions, never to log message bodies.

**Test**: `tests/test_renderer.py` (new TEST-030) — parametrized over every `code` enum value, asserts (a) the rendered string equals `envelope.message_user` exactly; (b) no 🟡 field substring appears in the rendered string; (c) the structured event is emitted with all required dimensions; (d) `LOG001` lint rejects forbidden interpolation patterns.

### 6.5 Document Schema Versioning (`schemaVersion`)

Cosmos documents carry an integer `schemaVersion` field (`sessions`, `users`, `topics`, `audit`). Migrator modules live in `src/data/migrations/migrate_{container}_v{from}_to_v{to}.py`.

**Read path**: repositories MUST switch on `schemaVersion` and call the appropriate migrator chain to reach the current version. Tests parametrize over the full version chain.

**Write path**: always writes the current `schemaVersion`. Never branches on it.

**Migration discipline**:
- Breaking schema changes (rename, type change, removal) require an ADR.
- Each migrator is pure (input doc → output doc), idempotent, and tested with a corpus of pre-migration documents under `tests/fixtures/migrations/`.
- The dual-write window is **not** supported in v1 — migrations are read-time only. Rollback is handled by deploying the prior code version (the migrator chain is monotonic; `v3 → v2` is not provided unless explicitly versioned).

### 6.6 Cross-reference to requirements

| Requirement                          | Contracted in                                                          |
| ------------------------------------ | ---------------------------------------------------------------------- |
| FR-003 (N from bank)                 | §1.5 `start_quiz`.                                                     |
| FR-008 (resume by session_id)        | §4.3 state machine.                                                    |
| FR-009 (channel switch)              | §4.3; `session.channel` is "most-recent", not enforced-once.           |
| FR-010/011/014 (language pref)       | §1.4 `set_language`; §2.2 `users` doc.                                 |
| FR-012 (language fallback)           | §1.5.5 fallback contract.                                              |
| FR-015 (server-side timers)          | §4.7.                                                                  |
| NFR-001 (~300 ms p95 in voice)       | §1.1 latency budget table.                                             |
| NFR-002 (idempotency, non-negotiable)| §1.6.5 + §4.4.                                                         |
| NFR-003 (reproducible shuffle)       | §1.5.7 (seed) + §2.1 (`seed`, `shuffledIds` persisted).                |
| NFR-004 (server-side timers)         | §4.7.                                                                  |
| NFR-009 (grading_event)              | §4.5.                                                                  |
| NFR-011 (one record per lang)        | §3.                                                                    |
| NFR-014 (TTS-friendly returns)       | Pervasive — §1.5.4 (text shape), §4.2 (`message_user`).                |
| SEC-001 (no answer leakage)          | §0.1 sensitivity tiers; §1.5.4 QuestionView allowlist; §3.3 projection; TEST-006. |
| SEC-002 (only submit_answer reads key)| §3.3.2 dedicated method.                                              |
| SEC-006 (etag idempotency)           | §1.6.5; §4.4.                                                          |
| SEC-009 ("what the LLM sees")        | §0.1 + every contract's sensitivity column.                            |
| SEC-010 (lang allowlist)             | §1.4, §1.5.2, §4.1.                                                    |

---

## 7. Verification Hooks

This document is testable. Each of the following tests **maps directly to a contract clause** so a regression in the document or in the code is caught:

| Contract clause                              | Test                                                                                   |
| -------------------------------------------- | -------------------------------------------------------------------------------------- |
| §0.1 sensitivity tiers (no 🟡 to LLM)        | `tests/test_no_answer_leakage.py` (TEST-006) — across `en`, `fr`, `es`.                |
| §1.5.4 QuestionView allowlist                | Same test, expanded: assert exact field set returned by `start_quiz` and `submit_answer.next`. |
| §1.6.5 conditional-write contract            | `tests/test_idempotency.py` (TEST-007) — real Cosmos primitive, not a mock.            |
| §1.6.6 idempotency table                     | Same test, parametrized across the 5 scenarios.                                        |
| §3.3 projection separation                   | Static lint: grep `selected_fields` calls in `question_search.py`; assert the LLM-path method does not include `correct_answer` in any call. |
| §4.3 state machine (forbidden transitions)   | `tests/test_session_state_machine.py` (new).                                           |
| §4.5 grading_event emission                  | TEST-010 (observability).                                                              |
| §4.7 timer enforcement                       | `tests/test_timers.py` — quiz expired mid-flow auto-grades remainder.                  |
| §5.x normalizer per-language                 | `tests/test_grading.py` + `tests/test_language_resolution.py`, parametrized.           |

If a future PR weakens any of these contracts, the corresponding test should turn red **before** the document changes.
