# DEV-STORY PROMPT — TASK-005 TOOLS (`src/agent/tools.py`) — CRITICAL PHASE

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 3 — Tool Layer (**THE MOST IMPORTANT PHASE** — the security boundary lives here)
**Current Task Pack**: 005-tools (the five Python tools, multilingual answer normalizer, TTS-friendly shaper, defensive `correct_answer` strip, coverage-fallback consent flow, server-side timers)
**Scope**: Implement the five tools the agent calls, plus the answer normalizer (`en`/`fr`/`es`), TTS-friendly response shaper, defensive recursive strip, coverage-fallback consent mechanics, and server-side timers. Every tool enforces ADR-005 (answer-leakage boundary) and the contracts in `specs/008-api-contracts.md`.

## TASK REFERENCES

- `tasks/005-tools.md`
  - TASK-080 — Tool I/O Pydantic models
  - TASK-081 — `list_topics(language)` tool
  - TASK-082 — `set_language(user_id, lang)` tool (ISO 639-1 allowlist validation)
  - TASK-083 — `start_quiz(user_id, topic, n, language, difficulty?)` tool
  - TASK-084 — `submit_answer(session_id, question_id, answer)` tool (**non-negotiable idempotency**)
  - TASK-085 — `get_results(session_id)` tool
  - TASK-086 — `src/agent/answer_normalizer.py` (multilingual)
  - TASK-087 — TTS-friendly response shaper
  - TASK-088 — Defensive `correct_answer` stripping
  - TASK-089 — Coverage-fallback surface mechanics (`suggest_fallback` helper)
  - TASK-189 — Coverage-fallback consent flow (agent-side, GOV-025)
  - TASK-090 — Server-side per-question + per-quiz timers
- Cross-pack dependencies:
  - `tasks/002-ai-search.md` TASK-027 (`get_question_view`, `get_answer_key`, `search_topic`)
  - `tasks/003-cosmos-db.md` TASK-042, TASK-043, TASK-045, TASK-046, TASK-047, TASK-048, TASK-049
  - `tasks/004-agent-framework.md` TASK-062, TASK-070 (dispatcher)
  - `tasks/007-security.md` TASK-123 (ISO 639-1 validator)

## SPEC REFERENCES

- `specs/003-data-contracts.md` — §3 (tool contracts), §5 (AppConfig values)
- `specs/004-agent-behavior.md` — §6 (normalisation), §7.2 (fallback consent)
- `specs/005-security-model.md` — SEC-001, SEC-002, SEC-006, SEC-010
- `specs/008-api-contracts.md` — §0.4 (snake_case), §1.5.3 (start_quiz wire shape), §1.5.4 (Question), §1.5.5 (E_NO_COVERAGE), §1.5.6 (E_SESSION_ACTIVE), §2.3 (TopicDoc), §2.4 (AuditEvent), §3.3 (search client), §4.7 (timers)
- `specs/009-agent-governance.md` — GOV-024, GOV-025, GOV-027, GOV-031, GOV-052, GOV-070
- `specs/006-testing-strategy.md` — TEST-003, TEST-004, TEST-005, TEST-006, TEST-007, TEST-010, TEST-022, TEST-024

## ADR REFERENCES

- `adr/005-tool-boundary-prevents-answer-leakage.md` — the boundary every tool enforces
- `adr/003-use-cosmos-db-for-session-state.md` — `submit_answer` uses `ifMatch` etag

## GOVERNANCE REFERENCES

- `docs/ai-agent-development-guidelines.md` — tool boundary, defensive strip, consent flow
- `docs/coding-standards.md` — Python async, type discipline, Pydantic v2 patterns
- `docs/llm-boundary.md` — what the LLM sees vs does not see
- `docs/content-governance.md` — phrasing-block authoring (drives copy in TASK-189)

## OBJECTIVE

Implement the tool layer so that:

1. Every tool I/O model is typed (Pydantic v2, snake_case), and the JSON schema generated from each response model **provably** omits `correct_answer` (asserted by test).
2. `list_topics(language)` returns localised labels filtered by per-language coverage (count > 0 in `topics.counts[topic][language]`).
3. `set_language(user_id, lang)` validates against the ISO 639-1 allowlist (TASK-123), upserts `users` doc, returns the echo.
4. `start_quiz` creates a session, seeds the shuffle (003-cosmos-db TASK-049), initialises server-side timers (TASK-090), returns Q1 (text + options only — no `correct_answer`, recursively). On zero coverage in requested language → `E_NO_COVERAGE` with `suggested_fallback` — **never** silent language switch. On count clamp → `fallback_notice` populated, no language change.
5. `submit_answer` is **provably idempotent** (NFR-002, SEC-006). Loads session with etag, validates state, runs language-aware normaliser, fetches answer key via the server-only AI Search method, grades deterministically (set comparison for multi-correct, `==` for single), appends via Cosmos conditional write (003 TASK-047), emits `grading_event` only on the successful write path.
6. `get_results` returns final score, percentage, pass/fail, per-question breakdown (`{question_id, verdict}` only — no question text, no answer key), all in the session language.
7. The multilingual answer normaliser handles position references, letter forms, option text, and voice fillers across `en`/`fr`/`es` with NFKD + accent-strip.
8. The TTS-friendly shaper produces sentence-length, markdown-free, phonetic-safe text with per-language option framing.
9. The defensive recursive strip removes `correct_answer`, `correctAnswer`, `answer_key` from every tool return; logs a warning when it had to act.
10. The coverage-fallback consent flow (TASK-189) enforces the two-turn dance: ask in the requested language → wait for user input → only on explicit affirmative call `set_language` and re-call `start_quiz`. No silent cross-language serve.
11. Server-side timers enforce per-question and per-quiz expiry; client never participates in the decision.

## IMPLEMENTATION RULES

- **Tool I/O snake_case** (per `008-api §0.4`). Cosmos docs are camelCase; the bridge happens in the Pydantic models from 003-cosmos-db TASK-045.
- **`Question` (public) has no `correct_answer` field**, ever. `QuestionWithAnswer` does not appear in any tool response model.
- **`submit_answer` is the ONLY function** that may import `get_answer_key`. The import lives inside the function body. AST lint enforces.
- **Defensive strip applied to every tool return** — recursive walk removes `correct_answer`, `correctAnswer`, `answer_key` from any nesting level. If keys were found, emit a warning (not error) to App Insights.
- **`grading_event` emitted only on the successful write branch** of `submit_answer`. Not on idempotent no-op. Not on expired-session auto-grade more than once per slot.
- **Coverage fallback decision tree**:
  - Coverage in requested language > 0 AND >= n → proceed normally.
  - Coverage in requested language > 0 AND < n → clamp `n` to coverage; populate `fallback_notice` (this is a COUNT clamp, not a LANGUAGE switch — no consent needed). Persist `requestedLanguage == language`.
  - Coverage in requested language = 0 → return `{ ok: false, error: { code: "E_NO_COVERAGE", detail: { requested, suggested_fallback } } }`. **Do NOT auto-switch.** Agent runs the consent flow (TASK-189).
- **`suggest_fallback(topic, requested_lang, n)`** order:
  1. User's previously-used language (`users.detectedLanguage` or last `sessions.language`).
  2. Topic's `defaultLanguage` (per `008-api §2.3`).
  3. Highest-coverage language with `count >= n`.
  4. Return `None` if no language has coverage.
- **Consent flow (TASK-189)** is two turns:
  - Agent surfaces gap in the **requested** (active) language using phrasing-block slot `coverage_gap_consent` (added in 004-agent-framework TASK-062).
  - Affirmative ("oui"/"yes"/"sí") → agent calls `set_language(user_id, suggested_fallback)` → re-calls `start_quiz` with new language. The fallback notice is read aloud in the user's requested language before Q1.
  - Negative → agent calls `list_topics(language=requested)` and offers a different topic.
  - Ambiguous → agent re-prompts once with the same phrasing-block slot.
  - **TEST-022 asserts `set_language` is called between the two `start_quiz` calls.**
- **Server-side timers** (TASK-090):
  - On `start_quiz`: set `startedAt`, `questionStartedAt`, `timeLimitSeconds`, `perQuestionLimitSeconds` (from AppConfig).
  - On `submit_answer`: recompute `now - questionStartedAt`. Exceeded → mark `unanswered`, advance.
  - If `now - startedAt > timeLimitSeconds` → flip to `Expired`; auto-grade remaining as `unanswered`; return final result with `done: true`.
- **Answer normaliser**:
  - Per-language map: position ("the first" / "la première" / "la primera"), letter form ("a" / "letra a" / "lettre a"), option keys ("A"/"a").
  - Fallback substring match against `option.text` (case- and accent-insensitive, NFKD).
  - Voice fillers stripped per language ("um", "uh", "euh", "este") in pre-processing.
  - No match → return `None`; the tool re-prompts politely.
- **TTS shaper**:
  - No `*`, `**`, `` ` ``, `#`, `[`, `]`, `~`, `_` in output.
  - Per-language option framing: `"Option A: <text>."` / `"Réponse A: <text>."` / `"Opción A: <text>."`.
  - Numerals 0–20 spelled out per language.
  - URLs replaced with phonetic-safe spoken form.
- **`get_results`** breakdown is aggregate-only: `{question_id, verdict}`. No question text, no answer key, no `explanation` in the breakdown payload. Pass/fail wording from phrasing blocks.
- **`set_language`** validates via 007-security TASK-123 validator (pulls live from AppConfig with cache).
- **`list_topics`** caches per-process with short TTL; filters out topics with zero question count in the requested language.

## OUTPUT FILES

Generate:

- `src/data/models.py` — extend with tool I/O models: `ListTopicsResponse`, `SetLanguageRequest`, `SetLanguageResponse`, `StartQuizRequest`, `StartQuizResponse`, `SubmitAnswerRequest`, `SubmitAnswerResponse`, `GetResultsResponse`, `Question` (public — no `correct_answer`), `ToolError` envelope.
- `src/agent/tools.py` — the five tool functions:
  - `async def list_topics(language: str) -> ListTopicsResponse`
  - `async def set_language(user_id: str, lang: str) -> SetLanguageResponse`
  - `async def start_quiz(user_id: str, topic: str, n: int, language: str, difficulty: str | None = None) -> StartQuizResponse`
  - `async def submit_answer(session_id: str, question_id: str, answer: str) -> SubmitAnswerResponse`
  - `async def get_results(session_id: str) -> GetResultsResponse`
- `src/agent/answer_normalizer.py` — multilingual normalizer with per-language maps + filler stripping
- `src/agent/tts_shaper.py` — `shape_question`, `shape_results`, `shape_topic_list`, `shape_verdict`
- `src/agent/defensive_strip.py` — recursive `strip_answer_key(payload) -> tuple[payload, found: bool]`
- `src/agent/coverage_fallback.py` — pure helper `suggest_fallback(topic, requested_lang, n) -> str | None`
- `src/agent/timers.py` — server-side timer helpers (`enforce_timers(session) -> SessionDoc`)
- `tests/unit/test_models_schema.py` — JSON schema of every response model asserts no `correct_answer`
- `tests/unit/test_answer_normalizer.py` — parametrised across `en`/`fr`/`es` with ≥10 variants per language
- `tests/unit/test_tts_shaper.py` — no markdown, option keys framed, numerals spelled
- `tests/unit/test_defensive_strip.py` — tainted record → clean payload + warning fired
- `tests/unit/test_coverage_fallback.py` — fallback-order rungs, `None` when no coverage
- `tests/integration/test_start_quiz.py` — happy path; count clamp; `E_NO_COVERAGE`; no `correct_answer` recursively
- `tests/integration/test_submit_answer.py` — single-correct, multi-correct, partial credit, expired session, normaliser-None re-prompt path
- `tests/integration/test_get_results.py` — final score; pass/fail wording in session language; no `correct_answer`
- `tests/integration/test_timers.py` — per-question expiry advances `unanswered`; per-quiz expiry flips to `Expired`
- `tests/integration/test_coverage_consent.py` — TEST-022 paths (affirmative → set_language → retry; negative → list_topics; ambiguous → re-prompt)

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **No answer leakage**: every tool response (recursively walked) contains no key matching `correct_answer`, `correctAnswer`, `answer_key`, and no value substring matching known seeded answer keys. Tainted-record injection test → defensive strip cleans + warning fires.
- **Idempotency (NFR-002 / SEC-006)**: two concurrent `submit_answer` calls for same `(session_id, question_id)` → exactly one persisted answer, one `grading_event`, one audit row; both callers receive identical verdicts.
- **Deterministic grading**: set comparison for multi-correct, `==` for single; same input → same verdict.
- **Multilingual normalisation**: "letra B" → `B` (es); "la deuxième" → `B` (fr); "uh, letter B" → `B` (en); "the green one" → `None`. NFKD + accent strip applied.
- **Coverage fallback (FR-012 / GOV-025)**: zero coverage in requested language → `E_NO_COVERAGE` + `suggested_fallback` (or `null`). Agent runs consent flow. **No silent serve.** TEST-022 verifies.
- **Server-side timers (NFR-004 / FR-015)**: client never participates in the timing decision. Expired session auto-grades remaining as `unanswered`.
- **TTS invariants (NFR-014)**: voice-channel output free of markdown, raw URLs; option keys framed per language; numerals 0–20 spelled.
- **AST check**: only `submit_answer` function body imports `get_answer_key`. Build fails on violation.
- **ISO 639-1 allowlist (SEC-010)**: disallowed codes rejected with clear error. Allowlist sourced from AppConfig via 007-security TASK-123 validator.
- **`grading_event` exactly once per persisted answer**: TEST-007 + TEST-010 assertions.

## FORBIDDEN ACTIONS

- Do NOT include `correct_answer`, `correctAnswer`, `answer_key`, or any synonym in any tool response model field, schema, or runtime payload. The recursive strip is the third line of defense — not the first.
- Do NOT import `get_answer_key` outside the body of `submit_answer`. The AST lint will fail the build.
- Do NOT auto-switch language on coverage gap. Always return `E_NO_COVERAGE` with `suggested_fallback`; let the agent run the consent flow per TASK-189. Implicit switches break GOV-025 and TEST-022.
- Do NOT switch language on a code-switched user utterance. The session language only changes via an explicit `set_language` call (GOV-027).
- Do NOT emit `grading_event` on the idempotent no-op return path of `submit_answer`. Doubles the metric, breaks TEST-007.
- Do NOT trust the client / agent / model to enforce timing. Timers are server-side; client never participates.
- Do NOT return `correct_answer` in `get_results` breakdown. The breakdown is `{question_id, verdict}` only. No question text, no explanation, no key.
- Do NOT include markdown (`*`, `**`, `` ` ``, `#`, `[`, `]`, `~`, `_`) in any tool return. The voice channel is the worst case; even text returns pass the lint.
- Do NOT include raw URLs in voice-channel returns. Replace with phonetic-safe spoken form.
- Do NOT include the question text in observability events. `grading_event` carries `received` (normalized key), not `receivedRaw`. `audit` rows carry both, but they live in 008-observability / 003-cosmos-db respectively.
- Do NOT log `correct_answer` values, even in error paths.
- Do NOT bypass the dispatcher (004-agent-framework TASK-070). Tools are invoked through `dispatch()` only.
- Do NOT implement Cosmos repository methods, AI Search client methods, or background sweeper logic in this pack — those live in 003 and 002 respectively.
- Do NOT silently advance the question on a `None` normaliser result. The tool re-prompts politely.
- Do NOT compute per-question pass/fail differently from the configured threshold (default 60%, configurable via `scoring:defaultPassThresholdPct` in AppConfig).
- Do NOT include user PII or transcripts in any return shape beyond what the spec defines.
