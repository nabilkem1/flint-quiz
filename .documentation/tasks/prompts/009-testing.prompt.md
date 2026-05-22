# DEV-STORY PROMPT — TASK-009 TESTING & EVALUATION

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 7 — Testing & Evaluation
**Current Task Pack**: 009-testing (every `TEST-*` ID from `specs/006-testing-strategy.md`, plus the multilingual validation matrix, voice normalisation tests, per-language Foundry Evaluation, and CI orchestration)
**Scope**: Tests are the load-bearing structure for the security boundary (answer leakage), idempotency contract, per-language quality, governance enforcement, and the GDPR cascade. Every GOV-* rule in `specs/009-agent-governance.md` maps to exactly one test in this pack.

## TASK REFERENCES

- `tasks/009-testing.md`
  - TASK-160 — `tests/test_no_answer_leakage.py` (TEST-006)
  - TASK-161 — `tests/test_idempotency.py` (TEST-007)
  - TASK-162 — `tests/test_grading.py` (multilingual)
  - TASK-163 — `tests/test_language_resolution.py`
  - TASK-164 — Smoke test text English (TEST-003)
  - TASK-165 — Smoke test text French (TEST-004)
  - TASK-166 — Smoke test voice Spanish (TEST-005)
  - TASK-167 — Foundry Evaluation per language (TEST-011)
  - TASK-168 — Negative tests (spoken-no-match, fallback, expired session, concurrent submit)
  - TASK-169 — Prompt injection test suite (delegates to 007 TASK-126)
  - TASK-170 — Observability test (TEST-010)
  - TASK-171 — Channel switch test (TEST-009)
  - TASK-172 — Resumption test (TEST-008)
  - TASK-173 — Multilingual validation matrix
  - TASK-174 — Voice normalization test
  - TASK-175 — CI orchestration (PR / merge / release pipelines)
  - TASK-176 — `tests/test_prompt_redaction.py` (TEST-018)
  - TASK-177 — `tests/test_tool_allowlist.py` (TEST-019)
  - TASK-178 — `tests/test_explanation_provenance.py` (TEST-020)
  - TASK-179 — `tests/test_refusal_localization.py` (TEST-021)
  - TASK-180 — `tests/test_coverage_consent.py` (TEST-022)
  - TASK-181 — `tests/test_injection_corpus.py` (TEST-023)
  - TASK-182 — `tests/test_tts_invariants.py` (TEST-024)
  - TASK-183 — `tests/test_prompt_hash.py` (TEST-025)
  - TASK-184 — `tests/test_session_state_machine.py` (TEST-026)
  - TASK-185 — `tests/test_timers.py` (TEST-027)
  - TASK-186 — `tests/test_gdpr_erasure.py` (TEST-028)
- Cross-pack dependencies: every preceding pack (001–008).

## SPEC REFERENCES

- `specs/006-testing-strategy.md` — every `TEST-*` ID; §1 (verification table), §7 (negative scenarios)
- `specs/008-api-contracts.md` — §0.4 (snake_case), §1.5.3, §1.5.5, §1.5.6, §2.4, §3.3, §4.3 (state machine), §4.5.1 (grading_event), §4.7 (timers)
- `specs/009-agent-governance.md` — GOV-003, GOV-010, GOV-012, GOV-024, GOV-025, GOV-027, GOV-031, GOV-050, GOV-052, GOV-060, GOV-061, GOV-070, GOV-072, GOV-091
- `specs/005-security-model.md` — SEC-001, SEC-006, SEC-007, SEC-008, SEC-010, SEC-014
- `specs/007-operational-runbook.md` — §8 (release pipelines), §9 (incidents)

## ADR REFERENCES

- `adr/005-tool-boundary-prevents-answer-leakage.md`
- `adr/003-use-cosmos-db-for-session-state.md`
- `adr/006-retention-policy.md`

## GOVERNANCE REFERENCES

- `docs/coding-standards.md` — pytest patterns, fixtures, parametrisation
- `docs/ai-agent-development-guidelines.md` — boundary tests, governance test discipline
- `docs/content-governance.md` — translation-drift signals (per-language eval)
- `infra/README.md` §10.2 — release-pipeline gates

## OBJECTIVE

Implement the full test suite + CI orchestration that:

1. Gates every PR/merge/release on the right tests in the right tier (T0/T1/T2/T3/T5/T6).
2. Asserts the answer-leakage boundary (TEST-006) across all three languages with tainted-record injection and AST-level lint.
3. Proves idempotency under real Cosmos concurrency (TEST-007).
4. Validates deterministic grading + multilingual normalisation (TEST-007 supporting).
5. Validates the language pipeline end-to-end: detection → persistence → propagation → coverage-consent fallback.
6. Drives en/fr text and Spanish voice smokes through the deployed agent.
7. Gates publishes via per-language Foundry Evaluation (TEST-011); blocks regressing languages only.
8. Implements every TEST-018..TEST-028 verification that maps to GOV-* rules.
9. Runs an adversarial injection corpus (plain + encoded payloads, en/fr/es) and asserts no leaks + hashed payload in `agent.injection_detected`.
10. Asserts state-machine forbidden transitions, server-side timer enforcement, sweeper-driven transitions.
11. Verifies the GDPR cascade (TASK-134) end-to-end with real Cosmos + real (or emulated) Key Vault: cascade, repeat, auth-negative, salt rotation.

## IMPLEMENTATION RULES

- **Multilingual matrix is parametrised against the AppConfig `languages:supported` allowlist**, NOT hard-coded constants. Adding a language and reindexing surfaces a per-language column in CI output.
- **TEST-006 is the load-bearing leak test** — gates every merge under `src/agent/tools.py`, `src/data/question_search.py`, `src/agent/quiz_agent.py`. Parametrised across en/fr/es. Tainted-record injection + AST check + recursive walk asserting no `correct_answer` / `correctAnswer` / `answer_key` keys AND no value substring matching seeded answer keys.
- **TEST-007 uses real Cosmos or the Cosmos emulator with `ifMatch` enabled** — never mocks the etag concurrency primitive. Asserts exactly one persisted answer, one `grading_event`, one audit row, identical verdicts to both callers, no-op on retry-after-success.
- **TEST-010 asserts both presence AND absence**: dimensions per `specs/008-api-contracts.md §4.5.1` present in the App Insights `grading_event`; `expected` and `receivedRaw` absent; the matching Cosmos `audit` row carries `expected` and `receivedRaw`.
- **TEST-011** runs per language on the seeded set on every reindex; blocks publish if any language regresses outside tolerance. Re-baseline yearly or on model change.
- **TEST-018** runs the static lint over every **rendered** prompt for each `(language, channel)` pair; asserts no forbidden tokens (seeded answer values, `_etag=`, `Bearer `, `AccountKey=`, `SharedAccessSignature`, `ApiKey=`, test-user PII fixtures).
- **TEST-019** uses synthetic tool-call request for `evil_tool` → assert dispatcher rejects + emits `agent.unknown_tool`. Concurrent `submit_answer` → dispatcher mutex returns identical cached result. Distinct from TEST-007 (Cosmos primitive).
- **TEST-020** asserts byte-equality between the stored `explanation` field and the value in the `submit_answer` response; asserts no synthesized text resembling an explanation appears when the field is empty.
- **TEST-021** asserts refusal copy comes from the active-language phrasing block (substring match against the slot); loop protection fires on the second consecutive refusal.
- **TEST-022** asserts: (a) mid-session "switch to Spanish" → `set_language("es")` called → next question in Spanish, already-answered stand; (b) coverage gap in `fr` for `en`-only topic → agent surfaces gap in French → consent → `set_language` → retry `start_quiz`; (c) code-switched utterance does NOT flip session language. Negative assertion: `set_language` was called between the two `start_quiz` calls.
- **TEST-023** corpus is a YAML file (`tests/fixtures/injection_corpus.yaml`) with rows `{id, language, encoding (plain|base64|rot13|leet), payload, expected_response_class}`. Asserts zero leaks, hashed payload in `agent.injection_detected`.
- **TEST-024** asserts no markdown, no raw URLs, language-specific option framing, numerals ≤ 100 spelled out, acronyms (VPN/TCP/IP) space-letter-expanded on first mention per session.
- **TEST-025** captures `session.promptHash` at start; 5 tool calls match; force a mismatch by mutating a phrasing block in-memory → assert 0 tool body invocations after mutation + `agent.prompt_hash_mismatch` event fired (P0).
- **TEST-026** drives a session through each terminal state; attempts every forbidden transition (`Scored→Active`, `Expired→Active`, `Completed→Active`); asserts rejection without state mutation. Every allowed transition advances and `_etag` updates.
- **TEST-027** uses `timeLimitSeconds=3`, `perQuestionLimitSeconds=2`; sleeps past per-question budget → `unanswered`; sleeps past per-quiz → `Expired` + remaining auto-graded `unanswered` + `done: true`. Sweeper case: silently-abandoned session past per-quiz budget flips to `Expired` on next 60-s tick.
- **TEST-028** seeds a user (1 `users` + 3 `sessions` + 5 `audit`); invokes `erase_user` with `group:flint-support-erasure` principal; asserts post-conditions; re-runs (dedup'd event); auth-negative without group → 403; salt rotation → `pseudo:v2:<hash>` distinct from v1.
- **CI pipeline tiers** (TASK-175):
  - **T0/T1 PR**: lint + unit + TEST-006 + grading + language-resolution + TEST-018 + TEST-019 + TEST-020 + TEST-021 + TEST-024.
  - **T1/T2 merge**: PR set + TEST-007 (real Cosmos) + TEST-010 + TEST-026 + TEST-027 + TEST-022.
  - **T2/T3/T5/T6 release**: full smoke matrix (TEST-003/004/005) + TEST-011 per-language + TEST-023 + TEST-025 + pre-public gate (007 TASK-130).
  - Each `TEST-*` ID is enumerated in exactly one tier.

## OUTPUT FILES

Generate:

- `tests/conftest.py` — shared fixtures (real Cosmos / emulator, language matrix from AppConfig)
- `tests/test_no_answer_leakage.py` (TEST-006)
- `tests/test_idempotency.py` (TEST-007)
- `tests/test_grading.py` (multilingual)
- `tests/test_language_resolution.py`
- `tests/smoke/test_text_en.py` (TEST-003)
- `tests/smoke/test_text_fr.py` (TEST-004)
- `tests/smoke/test_voice_es.py` (TEST-005)
- `tests/eval/test_foundry_evaluation.py` (TEST-011, per-language gate)
- `tests/test_negative_scenarios.py` (TASK-168 cases)
- `tests/test_prompt_injection.py` (TASK-169, delegates to 007 TASK-126)
- `tests/test_observability.py` (TEST-010)
- `tests/test_channel_switch.py` (TEST-009)
- `tests/test_resumption.py` (TEST-008)
- `tests/test_multilingual_matrix.py` (TASK-173)
- `tests/test_voice_normalization.py` (TASK-174)
- `tests/test_prompt_redaction.py` (TEST-018)
- `tests/test_tool_allowlist.py` (TEST-019)
- `tests/test_explanation_provenance.py` (TEST-020)
- `tests/test_refusal_localization.py` (TEST-021)
- `tests/test_coverage_consent.py` (TEST-022)
- `tests/test_injection_corpus.py` (TEST-023)
- `tests/fixtures/injection_corpus.yaml`
- `tests/test_tts_invariants.py` (TEST-024)
- `tests/test_prompt_hash.py` (TEST-025)
- `tests/test_session_state_machine.py` (TEST-026)
- `tests/test_timers.py` (TEST-027)
- `tests/test_gdpr_erasure.py` (TEST-028)
- `.github/workflows/ci-pr.yml` — T0/T1 PR pipeline
- `.github/workflows/ci-merge.yml` — T1/T2 merge pipeline
- `.github/workflows/ci-release.yml` — T2/T3/T5/T6 release pipeline
- `tests/README.md` — verification ID → test file → governance ID mapping

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **TEST-006** green across all three languages; tainted-record injection cleaned; AST check rejects `get_answer_key` outside `submit_answer`.
- **TEST-007** green on real Cosmos / emulator; exactly one persisted answer, one event, one audit row under N=2 (and TASK-131 reinforcement N=20) concurrency.
- **TEST-003/004/005** smokes complete end-to-end with final score and persisted session reaching `Scored`; voice dashboard reports tool-call p95 within budget.
- **TEST-008/009** resumption and channel-switch preserve language + state.
- **TEST-010** event count = answer count; required dimensions present; `expected`/`receivedRaw` absent from App Insights, present in `audit`.
- **TEST-011** per-language eval blocks publish on regression; deliberately ambiguous question fails the gate for the affected language only.
- **TEST-018..TEST-028** all green; each maps to exactly one GOV-* rule per `specs/006-testing-strategy.md §1`.
- **Multilingual matrix** derived from AppConfig allowlist; adding a language surfaces a per-language column in CI.
- **GDPR (TEST-028)**: all five sub-tests green; real Cosmos + real (or emulated) Key Vault; no mocks for the cascade itself.
- **CI orchestration**: each TEST-* ID enumerated in exactly one pipeline tier; PRs blocked on PR-pipeline failures; release blocked on any release-pipeline failure.

## FORBIDDEN ACTIONS

- Do NOT mock the Cosmos `ifMatch` etag primitive in TEST-007. Use real Cosmos or the emulator with `ifMatch` enabled.
- Do NOT hard-code the language list as a constant in tests. The matrix parametrises against AppConfig `languages:supported`.
- Do NOT assert only on the keyword `correct_answer` in TEST-006. Also assert no value substring matches the seeded answer values — the recursive walk catches a renamed-key regression.
- Do NOT mock the GDPR cascade for TEST-028. Use real Cosmos + real (or emulated) Key Vault; mocks here would hide salt-versioning and idempotency bugs.
- Do NOT skip the AST check on `get_answer_key`. Brittle to refactors is acceptable; a real leak is not.
- Do NOT trust App Insights ingestion to be immediate. TEST-010 waits up to 2 minutes.
- Do NOT include `correct_answer` values in fixture files committed to the repo without a separate audit boundary. Use opaque IDs; the test asserts on the answer value via the seeded `AnswerKey`.
- Do NOT register the same TEST-* ID in more than one pipeline tier. Each ID lives in exactly one tier.
- Do NOT bypass the per-language correctness gate by re-baselining on every model change without review. Re-baseline yearly or on model change with explicit review.
- Do NOT run release-pipeline tests on every PR (cost). Release tier runs on tag only.
- Do NOT include user PII fixtures with real PII. Use clearly synthetic fixtures.
- Do NOT implement the runtime code (tools, agent, repository, voice runtime) in this pack — those live in 004–006. This pack tests them.
- Do NOT skip the negative scenarios in TASK-168. Cheap-but-valuable: spoken no-match, fallback, expired session, concurrent submit.
- Do NOT mark TEST-019 (dispatcher) as `xfail` once 004 TASK-070 lands. Until then, mark `xfail` is acceptable.
