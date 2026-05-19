# 006 — Testing Strategy

- **Version**: v1.1 (TEST-018..TEST-027 promoted to first-class IDs per audit P0)
- **Last reviewed**: 2026-05-17
- **Owner**: QA + Security
- **Status**: Accepted

This document defines the verification plan, automated tests, and per-language evaluation strategy for v1.

## 1. Verification Plan (End-to-End)

| ID        | Step                                                                                                                                                                |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TEST-001  | **Provision**: `azd up` from a clean subscription → all resources deploy via Bicep.                                                                                 |
| TEST-002  | **Seed**: `python src/seed/seed_index.py` → ≥30 questions × 3 languages across 3 topics indexed in AI Search.                                                       |
| TEST-003  | **Smoke test — text, English**: "Start a 5-question quiz on Azure Networking" → agent asks Q1, complete the flow, verify final score and Cosmos session row.        |
| TEST-004  | **Smoke test — text, French**: "Pose-moi 5 questions sur le réseau Azure" → flow runs in French end-to-end; AI Search filter shows `language=fr`.                   |
| TEST-005  | **Smoke test — voice, Spanish**: connect to Realtime endpoint, ask in Spanish, complete 3 questions by voice → audio response in Spanish; TTS-friendly phrasing; answer normalization handles spoken variants. |
| TEST-006  | **Security — no answer leakage** (automated): `pytest tests/test_no_answer_leakage.py` — assert tool return JSON contains no `correct_answer` field across all language variants. |
| TEST-007  | **Idempotency**: simulate duplicate `submit_answer` calls → score increments exactly once.                                                                          |
| TEST-008  | **Resumption**: disconnect mid-quiz (text or voice), reconnect with same `session_id` → continue from next unanswered question.                                     |
| TEST-009  | **Channel switch**: start a quiz in voice, finish it in text (same `session_id`) → seamless.                                                                        |
| TEST-010  | **Observability check**: App Insights shows one `grading_event` per `submit_answer` with all dimensions (sessionId, questionId, language, channel, expected, received, verdict, latencyMs). |
| TEST-011  | **Foundry Evaluation — per language**: run a per-language question-bank quality evaluation against the seeded set; assert parity within tolerance.                  |
| TEST-018  | **Prompt redaction lint** (GOV-005): asserts no forbidden token (answer-key strings, PII, etag, secrets) appears in any rendered prompt layer (identity / contract / phrasing block / session frame). Run on every change under `src/agent/prompts/`. |
| TEST-019  | **Tool allowlist + no parallel `submit_answer`** (GOV-010, GOV-012): the dispatcher rejects (a) any tool name not in the registered five and (b) concurrent calls for the same `(session_id, question_id)` — the second caller receives the cached in-flight result. |
| TEST-020  | **Explanation provenance** (GOV-031): explanation in tool returns is present **iff** the question record carries an `explanation` for the active language; never synthesized. |
| TEST-021  | **Refusal localization** (GOV-071, GOV-072): refusal copy is sourced from the active-language phrasing block per GOV-071; never English-by-default in an `fr`/`es` session; soft-decline vs hard-refuse paths per GOV-072 verified. |
| TEST-022  | **Language switch + coverage-fallback consent** (GOV-024, GOV-025): a mid-session switch goes through `set_language`; a topic with no coverage in the active language surfaces an explicit consent prompt before switching language — never silent cross-language serve. |
| TEST-023  | **Injection corpus** (GOV-060, GOV-061): adversarial prompts in English, French, Spanish, and encoded variants (base64, ROT13, leetspeak) do not extract answer keys, prompt content, or cross-session data; `agent.injection_detected` event fires with hashed payload. |
| TEST-024  | **TTS-safe rendering invariants** (GOV-050): voice-channel output contains no markdown chars, no raw URLs, options framed as `"Option A:"`, numerals spelled when ≤ 100, acronyms expanded on first mention. |
| TEST-025  | **Prompt-hash stability across a session** (GOV-003): the composed prompt SHA-256 is fixed at session start, written to the session row, and verified on every subsequent tool call; a forced mismatch produces a P0 halt + alert. |
| TEST-026  | **Session state machine** (`008-api §4.3`): forbidden transitions (`Scored→Active`, `Expired→Active`, `Completed→Active`, same-state no-ops) are rejected; allowed transitions advance per the table. |
| TEST-027  | **Server-side timer enforcement** (`008-api §4.7`, FR-015, NFR-004): a `submit_answer` past the per-question budget auto-grades the question as `unanswered`; a `submit_answer` past the per-quiz budget flips status to `Expired` and auto-grades remaining as `unanswered`. |
| TEST-028  | **GDPR right-to-erasure cascade** (SEC-008 / [ADR-006](../adr/006-retention-policy.md)): erase a user; `users` row gone, all `sessions` for that user gone, `audit` rows pseudonymized (`userId = pseudo:...`), `audit.user_erased` event present. Re-running the cascade is idempotent. Unauthorised callers rejected. |
| TEST-029  | **Audit completeness — symmetric to TEST-010** (`008-api §4.5`): on every persisted answer, assert the Cosmos `audit` row carries all required fields **including** `expected` and `receivedRaw`. Confirms the two-sink contract (App Insights 🟢-only / `audit` 🟡-allowed) cannot regress to a "no expected anywhere" state without detection. |
| TEST-030  | **Error envelope rendering layer** (`008-api §6.4`): parametrized over every `code` enum value (§4.2.2), assert (a) `Renderer.render_error(envelope)` returns exactly `envelope.message_user`; (b) no substring of any 🟡 field (`message_dev`, `detail`) appears in the rendered string; (c) `agent.tool_error` event is emitted with `correlation_id`, `code`, `tool_name`, `class`; (d) `LOG001` lint rejects `f"{error}"`-style envelope interpolation. |

## 2. Automated Test Suite

| Test File                                  | Purpose                                                                                                                            | Coverage              |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| `tests/test_no_answer_leakage.py`          | Asserts `correct_answer` never appears in any tool return, all languages.                                                          | TEST-006, SEC-001     |
| `tests/test_idempotency.py`                | Asserts double-submit doesn't double-score (Cosmos `ifMatch` etag behavior on `(session_id, question_id)`).                        | TEST-007, NFR-002, SEC-006 |
| `tests/test_grading.py`                    | Deterministic grader correctness across answer shapes: single-correct, multi-correct, partial credit, spoken variants.             | FR-013, NFR-014       |
| `tests/test_language_resolution.py`        | Asserts language preference flows through `start_quiz` → AI Search filter → tool returns. Includes fallback when topic lacks coverage. | FR-010/FR-011/FR-012  |
| `tests/test_prompt_redaction.py`           | Static lint over every prompt layer: no answer-key strings, no PII, no secrets/etags.                                              | TEST-018, GOV-005     |
| `tests/test_tool_allowlist.py`             | Dispatcher rejects unregistered tool names; concurrent `submit_answer` for the same `(session_id, question_id)` is serialized.     | TEST-019, GOV-010, GOV-012 |
| `tests/test_explanation_provenance.py`     | Explanation only appears when bank provides one for the active language; never synthesized.                                        | TEST-020, GOV-031     |
| `tests/test_refusal_localization.py`       | Refusal copy is in the active language and sourced from the phrasing block.                                                        | TEST-021, GOV-071, GOV-072 |
| `tests/test_coverage_consent.py`           | Mid-session language switch + coverage-gap fallback both require explicit consent flow.                                            | TEST-022, GOV-024, GOV-025 |
| `tests/test_injection_corpus.py`           | Adversarial prompts (en/fr/es + encoded) do not leak; `agent.injection_detected` fires with a hashed payload.                      | TEST-023, GOV-060, GOV-061 |
| `tests/test_tts_invariants.py`             | TTS-safe rendering: no markdown, no raw URLs, "Option A:" framing, numeral expansion.                                              | TEST-024, GOV-050, NFR-014 |
| `tests/test_prompt_hash.py`                | Composed prompt SHA-256 is stable across a session; mid-session mismatch halts + alerts.                                           | TEST-025, GOV-003     |
| `tests/test_session_state_machine.py`      | Forbidden transitions rejected; allowed transitions advance.                                                                       | TEST-026, `008-api §4.3` |
| `tests/test_timers.py`                     | Per-question + per-quiz server-side timer enforcement.                                                                             | TEST-027, FR-015, NFR-004 |
| `tests/test_gdpr_erasure.py`               | End-to-end deletion cascade: hard-delete `users` + `sessions`, pseudonymize `audit`, idempotent re-run, authorisation enforcement. | TEST-028, SEC-008, ADR-006 |
| `tests/test_audit_completeness.py`         | Symmetric to TEST-010: every persisted answer writes an `audit` row containing `expected` and `receivedRaw`. Prevents silent two-sink drift.       | TEST-029, NFR-009, SEC-014 |
| `tests/test_renderer.py`                   | Error envelope renderer surfaces only `message_user`; 🟡 fields never reach LLM string; `agent.tool_error` event correctness; `LOG001` lint.       | TEST-030, SEC-001, 008-api §6.4 |

## 3. Test Discipline

- **Tool boundary tests run on every change to `src/agent/tools.py`**. The answer-leakage assertion is the most important property in the system — make it impossible to regress silently.
- **Idempotency test exercises the real Cosmos contract**: the test must use the actual etag concurrency primitive, not a mock that doesn't enforce conditional writes.
- **Grading tests are language-aware**: each test case is parametrized across `en`/`fr`/`es` to catch normalizer regressions per language.

## 4. Per-Language Quality Evaluation

**Foundry Evaluations** on the question bank itself — drift, ambiguity, answer-key correctness over time, per-language quality parity. This is the non-obvious Foundry-native win for an exam system.

- Runs **per language** (NFR-010).
- Gates publishes: a per-language regression in difficulty, ambiguity, or correctness blocks an index update.
- Out of the hot path — evaluations do not run inline during a quiz session.

Why this matters (Risk 3 in [001-product-requirements §7](./001-product-requirements.md)): translating a question can change its difficulty, ambiguity, or correctness. Without per-language evaluation, drift accumulates silently.

## 5. Observability as a Test Surface

Beyond functional tests, the system's correctness is partly observed via:

- **Grading event stream**: every `submit_answer` emits to two sinks with different shapes (see [008-api-contracts §4.5](./008-api-contracts.md)) — App Insights `grading_event` (no answer keys, no raw PII) and Cosmos `audit` (server-only, carries `expected` and `receivedRaw`). TEST-010 verifies emission and the absence of `expected`/`receivedRaw` from App Insights; ongoing monitoring tracks correctness rate per language.
- **Voice-specific metrics**: STT latency, TTS latency, tool-call round-trip in voice mode — separate dashboard.

See [007-operational-runbook](./007-operational-runbook.md) §2 for the operational dashboards.

## 6. Smoke-Test Matrix

| Channel | Language | Topic           | Verifies                                              |
| ------- | -------- | --------------- | ----------------------------------------------------- |
| Text    | English  | Azure Networking | TEST-003, basic happy path                            |
| Text    | French   | Réseau Azure    | TEST-004, multilingual filter end-to-end              |
| Voice   | Spanish  | Redes Azure     | TEST-005, Realtime + normalizer + TTS-friendly shape |

Extending the matrix is cheap (new language = author + reindex + add row).

## 7. Negative Tests Worth Adding (low cost)

- Spoken answer that doesn't match any option ("the green one") → normalizer returns a non-match; agent re-prompts politely.
- Topic requested in a language with no coverage → agent falls back per FR-012 with explicit notice.
- `submit_answer` against an expired session → server rejects, status flips to `Expired`, remaining auto-graded as unanswered.
- Concurrent `submit_answer` calls on the same `(session_id, question_id)` → exactly one succeeds; the other surfaces a no-op (idempotent) result.
