# Spec Kit Consistency Audit — Flint Quiz

**Date**: 2026-05-17
**Scope audited**: `specs/` (9 docs), `adr/` (5 ADRs), `tasks/` (10 packs), `tests/specs/testing-matrix.md`, `infra/README.md`, `docs/initial-plan.md`, `docs/refactor-summary.md`.
**Method**: full read of every artifact; cross-reference along seven audit dimensions (conflicts, orphans, missing deps, undefined terminology, architecture contradictions, security inconsistencies, missing test coverage).

The Flint Quiz spec kit is **substantially coherent**. The core security/idempotency thesis — answer key never enters LLM context (SEC-001/SEC-002), grading via deterministic Python with Cosmos `ifMatch` (NFR-002/SEC-006), per-language records (NFR-011) — is restated consistently across specs, ADRs, tasks, and tests. The risk register, ADRs, FR/NFR/SEC/TEST traceability IDs, and the test matrix are mature.

However, the kit has accumulated layering artifacts as new docs (`008-api-contracts.md`, `009-agent-governance.md`, `infra/README.md`) added detail that the earlier docs (`003-data-contracts.md`, `006-testing-strategy.md`, `tasks/`) have not absorbed. The result is **numeric defaults that disagree**, **field names that drift**, **GOV-* requirements with no implementing tasks**, and **TEST-018..TEST-025 referenced but never defined**. None of these are architectural showstoppers; all are blockers to a clean v1 implementation if left unresolved.

The audit groups findings by severity. **P0 = will produce wrong behavior or break a security invariant if implemented as written. P1 = will cause an implementation halt or rework. P2 = cosmetic / doc rot.**

---

## 1. Inconsistencies Found

### 1.1 Conflicting numeric defaults (P0)

These produce mismatched configuration unless reconciled before implementation.

| # | Concept | Source A | Source B | Source C | Recommended resolution |
|---|---------|----------|----------|----------|------------------------|
| C1 | **Pass threshold** | `008-api-contracts.md` §2.1 `passThresholdPct: default 60` and §1.6.11 example `60.0` | `tasks/005-tools.md` TASK-085: "configurable threshold; **default 70%**" | `docs/refactor-summary.md` §5 #1 flags this as an unresolved ambiguity | Pin **60% default**, configurable per-topic in `topics` container, override-able via AppConfig. Update TASK-085. |
| C2 | **Session TTL after Scored** | `008-api-contracts.md` §2.1: `7776000` s = **90 days** | `tasks/003-cosmos-db.md` TASK-050: "default **30 days**; configurable" | `infra/README.md` §12.1: "**30 days** after Completed/Expired" | Pin **30 days hot TTL** (per infra spec) for `sessions`. Update §2.1 of api-contracts. |
| C3 | **Audit retention** | `tasks/003-cosmos-db.md` TASK-051: "default **180 days**" | `008-api-contracts.md` §2.4: "TTL: **365 days** default" | `infra/README.md` §12.1: "**7 years**" | Compliance/dispute window drives this. Pin **7 years archive** (per infra) with the **365-day hot Cosmos TTL** triggering archive-to-Blob, then deletion. Document the two-stage policy. |
| C4 | **Shuffle seed algorithm** | NFR-003 + §02 §10: `seed = hash(session_id)` | `008-api-contracts.md` §1.5.7: `seed = sha256(session_id ‖ server_nonce)[:16]` | `tasks/003-cosmos-db.md` TASK-049: `hashlib.sha256(session_id.encode()).hexdigest()` (no nonce) | Choose: keep determinism reproducible from `session_id` alone (drop the nonce in §1.5.7) **OR** persist the nonce on the session row and amend NFR-003. Recommended: drop the nonce — reproducibility from `session_id` is simpler and the security threat the nonce defends against (predicting another user's shuffle) is implausible. |
| C5 | **Per-question time-limit field name** | `008-api-contracts.md` §2.1: `perQuestionTimeLimitSeconds` | `tasks/005-tools.md` TASK-090: `perQuestionLimitSeconds` (Cosmos field name) | none | Pick one. Recommended: `perQuestionLimitSeconds` (shorter; consistent with `timeLimitSeconds` already used for the quiz-level field). Update §2.1. |
| C6 | **Voice idle behavior** | `009-agent-governance.md` §2.6 GOV-014: "Voice 30s → **re-prompt once, then end channel**" | `tasks/006-voice-realtime.md` TASK-105: "if no audio for 30s, **close the connection**" | none | Both are correct individually but TASK-105 doesn't model the re-prompt. Amend TASK-105 implementation: first idle threshold = re-prompt; second = close. |

### 1.2 Field-name / type drift across docs (P1)

The "same" concept is named three different ways in three different places. Any implementation will need to pick one and risk silent schema mismatches.

| # | Concept | Variants |
|---|---------|----------|
| C7 | Requested-vs-resolved language on session | `008-api §2.1`: `requestedLanguage` + `language` (resolved); `003-data §4.1` example: just `language`; `tasks/005-tools TASK-089`: `fallbackLanguage` |
| C8 | Fallback notice key | `008-api §1.5.3`: `fallback_notice` (snake_case); `tasks/005-tools TASK-083`: `fallbackNotice` (camelCase) |
| C9 | Per-language explanation field | `003-data §2.1`: single `explanation` (per-record per-language because record is per-language); `009-gov §4.2` and `§16.2`: `explanation_{lang}`, `explanation_en`, `explanation_fr` (multi-field per record) |
| C10 | Public/server-only AI Search method names | `adr/005`: "public method / server-only method"; `tasks/002 TASK-027`: `get_question_public` / `get_question_with_answer_server_only`; `008-api §3.3`: `get_question_view` / `get_answer_key` |
| C11 | Results envelope type | `003-data §6`: `Result`; `008-api §1.6.3`: `ResultsSummary`; `008-api §1.7`: `GetResultsOutput = ResultsSummary` |
| C12 | "Already graded" status | `009-gov §2.5`: `409 ALREADY_GRADED`; `008-api §4.2.2` defines `E_QUESTION_OUT_OF_ORDER` but no `ALREADY_GRADED` code — instead the stale-replay path returns `ok: true` with the existing verdict |
| C13 | `session.channel` presence | `008-api §2.1`: `channel: "text"|"voice"` on session; `003-data §4.1`: not in the conceptual fields list |

### 1.3 Convention drift between specs and code-shape (P2)

| # | Issue | Source |
|---|-------|--------|
| C14 | `003-data §3.1` says tools return `{question_id, text, options[], metadata}` — but the canonical `QuestionView` in `008-api §1.5.4` has **no `metadata` field**, only `difficulty`. The earlier mention is misleading. |
| C15 | `003-data §3.1` lists "single-correct" and "multi-correct" implicitly; `008-api §1.6.4` makes it explicit via the set-comparison branch. Earlier spec is silent on partial credit, which `008-api §1.6.4` defines as `score_weight × (|normalized| / |correct|)` — only one place names the formula. |
| C16 | `003-data §3` lists 5 tools; ADR-005 lists 2 search methods; `008-api §1.5.6` mentions an undefined `abandon_quiz` action — but GOV-010 says "only five tools, anything else fails closed". |
| C17 | Naming case: `003-data §4.4 audit` doc uses `partitionKey: /sessionId`; `008-api §2.4 AuditEvent.sessionId` is camelCase; consistent. But the field is called `session_id` (snake) in tool-facing inputs throughout `008-api §1`. Pydantic models need explicit alias rules. |

---

## 2. Missing Requirements / Orphan IDs

### 2.1 GOV-* without implementing tasks (P0 for governance coverage)

The 009 governance doc adds GOV-001..GOV-104 but most have no corresponding task in `tasks/`.

| GOV ID | Requirement | Implementing task? |
|--------|-------------|--------------------|
| GOV-001..GOV-005 | Layered system prompt, immutability, prompt versioning by hash, per-language phrasing discipline, forbidden prompt content | Partial — `tasks/004 TASK-062` defines the prompt but does not implement layering, hashing, or version verification. **No task for the prompt-hash check (GOV-003).** |
| GOV-010..GOV-014 | Tool allowlist, argument sourcing, no parallel calls, retry/timeout discipline | `tasks/004 TASK-063` registers 5 tools but **no dispatcher-level allowlist enforcement** that "fails closed" on a 6th tool. No task for the parallel-call rejection (GOV-012). |
| GOV-020..GOV-027 | Active language definition, mid-session switching, coverage-gap consent flow, code-switch handling | Partial — `tasks/005 TASK-089` implements fallback **without consent flow** (GOV-025 requires consent). |
| GOV-030..GOV-034 | No hallucination, no preview, score read-back discipline | No dedicated task; relies on prompt instruction alone. |
| GOV-060..GOV-063 | Prompt-injection detection / log / response | `tasks/007 TASK-126` tests injection but no task implements the `agent.injection_detected` event emitter with hashed payload. |
| GOV-080..GOV-083 | Session-scope memory boundaries | Implicit; no explicit task or test. |
| GOV-090..GOV-092 | Text formatting, length cap (600 tokens), no self-reference | No task implements the 600-token cap or the `agent.output_truncated` event. |
| GOV-103, GOV-104 | Audit-trail visibility on dispute; null-key skip semantics | `OptionKey = null` for skip conflicts with §0.2 type (`A`..`Z` only). See §3.2 below. |

### 2.2 Tests referenced but not defined (P0 — release-gate gap)

`specs/009-agent-governance.md §17` lists **TEST-018 through TEST-025** and says "PRs that modify GOV-### IDs without updating tests fail CI." But:

- `specs/006-testing-strategy.md §1` defines only **TEST-001..TEST-011**.
- `tests/specs/testing-matrix.md` does not define TEST-018..TEST-025 either.
- `tasks/009-testing.md` has no TASK enumerating these tests.

Missing tests:

| Test ID | Covers GOV | Description (from 009-gov §17) |
|---------|------------|--------------------------------|
| TEST-018 | GOV-005 | Prompt-redaction lint (no banned tokens in any prompt layer) |
| TEST-019 | GOV-010, GOV-012 | Tool allowlist + no parallel `submit_answer` |
| TEST-020 | GOV-031 | Explanation only when bank provides it for active language |
| TEST-021 | GOV-052, GOV-072 | Refusal copy from phrasing block in active language |
| TEST-022 | GOV-024, GOV-025 | Language switch + coverage-fallback consent flow |
| TEST-023 | GOV-060, GOV-061 | Injection corpus (en/fr/es/encoded) |
| TEST-024 | GOV-050 | TTS-safe rendering invariants |
| TEST-025 | GOV-003 | Prompt-hash stability across a session |

`008-api-contracts.md §7` additionally references **`tests/test_session_state_machine.py`** and **`tests/test_timers.py`** which are not enumerated as tasks.

### 2.3 Missing implementation tasks for documented architecture

| # | Item | Where promised | Where implemented |
|---|------|----------------|-------------------|
| O1 | Background sweeper job (Functions or scheduled job) that flips silently-abandoned `Active` → `Expired` | `008-api §4.3`, §4.7 | **No task** — neither in `tasks/003-cosmos-db.md` nor in `tasks/004-agent-framework.md` |
| O2 | Inactivity heartbeat job (`Active → Paused` after threshold) | `008-api §4.3` | No task |
| O3 | `agent.injection_detected`, `agent.coverage_gap`, `agent.refusal_loop`, `agent.unknown_tool`, `agent.output_truncated` event emitters | `009-gov §15` escalation table | Not in `tasks/008-observability.md` (which only enumerates `grading_event`) |
| O4 | Prompt-hash composition + verification | `009-gov §1.2` GOV-003 | Not in `tasks/004-agent-framework.md`'s system-prompt task |
| O5 | Output-length cap + truncation logging | `009-gov §10.2` GOV-091 | No task |
| O6 | `docs/llm-boundary.md` (SEC-009) | `tasks/007 TASK-128` says "Author docs/llm-boundary.md" but the file does not exist | Authoring task is open |
| O7 | `docs/secrets.md`, `docs/retention.md`, `docs/pre-public-gate.md`, `docs/playground.md`, `docs/rollback.md` | Referenced in `tasks/007 TASK-122`, `TASK-130`, `TASK-132`; `tasks/010 TASK-186`, `TASK-190` | None of these files exist yet |
| O8 | GDPR right-to-erasure flow (cascade `users` + `sessions`, pseudonymize `audit`) | `infra/README §12.2 rule 4` | No task or test |
| O9 | "Phantom" tool `abandon_quiz` (referenced in `008-api §1.5.6`) | `008-api §1.5.6` | Either define a sixth tool or change the resolution path. Best: change to "agent prompts user to wait for the existing session to time out" or add a sixth tool with full contract — **but GOV-010 forbids a sixth tool**. Resolve before implementation. |

### 2.4 Requirements without forward traceability

The testing matrix's coverage map is the best in the repo, but the following are not explicit there:

| Req | Coverage status |
|-----|-----------------|
| FR-011 | Tests: ML-001 only. Light coverage — language **detection** specifically (vs explicit set) deserves more cases. |
| NFR-013 | Only `VOX-006` (session cap). Per-minute cost discipline + dead-air detection lack a dedicated chaos test. |
| SEC-008 | `SECT-008`, `RES-005`. No test of voice transcript retention specifically; only session-row TTL. |
| SEC-012 | Marked n/a (out of v1 scope) — consistent. |
| NFR-014 | Pervasive coverage in `VOX-*` + `UT-012`; well covered. |

---

## 3. Recommended Corrections

### 3.1 Reconcile numeric defaults (one-line edits)

| # | File | Edit |
|---|------|------|
| Fix-1 | `specs/008-api-contracts.md` §2.1 | Change `passThresholdPct` default from "60" to align with the chosen value, **or** keep 60 and fix `tasks/005 TASK-085` to say 60. |
| Fix-2 | `specs/008-api-contracts.md` §2.1 | Change `ttl` description from `7776000` (90d) to `2592000` (30d) to match infra spec + cosmos-db task. |
| Fix-3 | `specs/008-api-contracts.md` §2.4 | Change `audit` default TTL from 365 days to align with infra's 7-year archive (either keep 365 as Cosmos hot, add archive step; or change to a different value). Document the two-stage policy explicitly. |
| Fix-4 | `specs/001-product-requirements.md` NFR-003 + `specs/002-system-architecture.md` §10 | Decide: keep `hash(session_id)` (simple) **or** amend to `hash(session_id ‖ server_nonce)` and persist `nonce` on the session row. Mirror the decision in `008-api §1.5.7` and `tasks/003 TASK-049`. |
| Fix-5 | `specs/008-api-contracts.md` §2.1 | Rename `perQuestionTimeLimitSeconds` to `perQuestionLimitSeconds` to match the task naming **OR** rename the task. Pick one. |
| Fix-6 | `tasks/006-voice-realtime.md` TASK-105 | Add the re-prompt-then-close behavior from `009-gov §2.6`. |

### 3.2 Reconcile field names (one-line edits)

| # | Edit |
|---|------|
| Fix-7 | Adopt `requestedLanguage` (resolved) + `language` (active) consistently. Drop `fallbackLanguage` from `tasks/005 TASK-089` — refer to `requestedLanguage` instead. Update `003-data §4.1` example to show both. |
| Fix-8 | Pick **snake_case** for tool inputs/outputs (matches `008-api §0.4`) and **camelCase** for Cosmos document fields. Apply uniformly. Pydantic models use aliases. |
| Fix-9 | Decide explanation strategy: keep **one `explanation` field per per-language record** (NFR-011 implies this — one record per `(logical_id, language)` pair). Then `explanation_{lang}` in `009-gov §4.2` is wrong — fix to "the question record's `explanation` field, populated for the active language because the record is per-language". |
| Fix-10 | Pick **one method-name pair** for the two-method search client. Recommended: `get_question_view` / `get_answer_key` (`008-api §3.3` is the most recent contract and explicit naming). Update `tasks/002 TASK-027` and `adr/005`. |
| Fix-11 | Pick **one results envelope name**. Recommended: `ResultsSummary` (matches the field tier table). Update `003-data §6` (`Result` → `ResultsSummary`). |
| Fix-12 | Add the missing `channel` field to `003-data §4.1` example to align with `008-api §2.1`. |
| Fix-13 | `OptionKey` is `A`..`Z` per `008-api §0.2`. Skip semantics in `009-gov §11.5 GOV-104` says `option_key = null` — but the type does not accept null. Resolve: introduce a distinct `OptionKey | null` union for the **input** path, or treat skip as `raw_answer = "skip"` and let the normalizer return `matched=None`. The latter is already how the normalizer works (`008-api §5.1` NormalizeResult.matched: `list[str] | None`). Amend GOV-104 to say "skip is `raw_answer="skip"` → normalizer returns `None` → grader records `verdict="unanswered"`". |

### 3.3 Close governance ↔ task gaps

Add tasks (or task amendments) covering:

- **TASK-XXX (in tasks/004)**: Prompt composition + SHA-256 hash + per-turn verification (GOV-001..GOV-003).
- **TASK-XXX (in tasks/004)**: Dispatcher allowlist enforcement: a list-checked dispatch layer in `quiz_agent.py` that rejects any tool name not in the registered five. Test: TEST-019.
- **TASK-XXX (in tasks/004)**: Parallel-call rejection on `(session_id, question_id)` at the dispatcher level (GOV-012). Returns the cached in-flight result.
- **TASK-XXX (in tasks/005)**: Fallback-with-consent flow (GOV-025). Today TASK-089 silently falls back; change to two-turn flow: surface gap → user consents/declines → execute.
- **TASK-XXX (in tasks/008-observability)**: Emit `agent.injection_detected`, `agent.coverage_gap`, `agent.refusal_loop`, `agent.unknown_tool`, `agent.output_truncated` custom events. Hash the raw payload for `agent.injection_detected`. Wire these to incident workbooks (`Security & Governance` workbook per infra §10.4).
- **TASK-XXX (in tasks/004)**: Output-length cap at 600 tokens + truncation event (GOV-091).
- **TASK-XXX (in tasks/003 or new pack)**: Background sweeper for `Active → Expired` and `Active → Paused` transitions, with `ifMatch` discipline (preserves SEC-006).
- **TASK-XXX (in tasks/007)**: GDPR right-to-erasure flow (deletion cascade on `users` + `sessions`; pseudonymize `userId` in `audit`).

### 3.4 Author missing reference docs

| File | Owner | Driving spec |
|------|-------|--------------|
| `docs/llm-boundary.md` | Security | SEC-009 / `tasks/007 TASK-128` |
| `docs/retention.md` | Platform | `tasks/007 TASK-132`; cite the C2/C3 resolved numbers |
| `docs/secrets.md` | Platform | `tasks/007 TASK-122` |
| `docs/pre-public-gate.md` | Security + Release | `tasks/007 TASK-130` |
| `docs/playground.md` | Platform | `tasks/010 TASK-186` |
| `docs/rollback.md` | Platform | `tasks/010 TASK-190` |

### 3.5 Define the missing tests as first-class TEST-* IDs

Promote TEST-018..TEST-025 into `specs/006-testing-strategy.md §1` so they are real, gateable artifacts, then enumerate them as tasks in `tasks/009-testing.md`.

Add to `tasks/009-testing.md`:

- TASK-176 → TEST-018: `tests/test_prompt_redaction.py`
- TASK-177 → TEST-019: `tests/test_tool_allowlist.py` + dispatcher parallel-call rejection
- TASK-178 → TEST-020: `tests/test_explanation_provenance.py`
- TASK-179 → TEST-021: `tests/test_refusal_localization.py`
- TASK-180 → TEST-022: `tests/test_coverage_consent.py`
- TASK-181 → TEST-023: `tests/test_injection_corpus.py` (extends TASK-126)
- TASK-182 → TEST-024: `tests/test_tts_invariants.py`
- TASK-183 → TEST-025: `tests/test_prompt_hash.py`
- TASK-184 → state-machine test (`tests/test_session_state_machine.py`)
- TASK-185 → timer-enforcement test (`tests/test_timers.py`)

(Renumber existing tasks/010 deployment tasks to avoid the TASK-180..TASK-192 collision in the current scheme.)

---

## 4. Architectural Weaknesses

### 4.1 Two AI Search round-trips inside `submit_answer` voice hot path

`submit_answer` voice budget is **250 ms p95** (`008-api §1.1`). The contract performs:

1. Cosmos point-read of session
2. AI Search `get_answer_key` (server-only)
3. Cosmos `ifMatch` conditional write
4. AI Search `get_question_view` for next question
5. App Insights event emit

That is **two Search calls + two Cosmos calls** per turn. Search is filtered point-reads (fast), but the latency budget is tight. If the answer-key fetch were avoided by **persisting just the verdict** in a pre-loaded structure, latency would improve — but doing so would mean caching the answer keys in the agent process memory, which is a security boundary tradeoff. **Recommendation: keep the current shape; add a latency test (LAT-001 already covers this) that fails the build if voice p95 exceeds 300 ms on a representative seed.**

### 4.2 No definition of how `start_quiz` finds candidate question IDs

`002-system-architecture.md` §4 sequence diagram says "filtered query (topic, language=fr, difficulty mix)" but `008-api §3.4` shows a `$top=200` filtered ID draw, then **application-side seeded shuffle**. If `topics.counts[language]` exceeds 200, **the seeded shuffle is operating on a Search-side-limited subset**, undermining auditability of the "all eligible IDs were considered" property.

**Recommendation**: either (a) document a higher `$top` ceiling matched to max realistic per-topic count, or (b) page through results and only then shuffle. Pick (a) as the simpler fix; document the ceiling and the assumption that no `(topic, language)` exceeds it.

### 4.3 Single agent serves Realtime endpoint — auth path is under-specified

`tasks/007 TASK-127` says "voice (Realtime) connection authenticates with Entra-issued tokens" but the WebRTC + Entra token-binding mechanism in Foundry Realtime is not described. The Realtime endpoint typically uses ephemeral session tokens; how those are bound to a user's Entra principal needs to be explicit. **Recommendation**: Add a paragraph to `specs/002 §9` describing the token-exchange flow, and a test (`SECT-005` already covers anon-rejection but not the binding).

### 4.4 No idempotency on `start_quiz` cleanup

`008-api §1.5.6` defines class `I-S` (idempotent via session lookup). But if the user's last `Active` session was never properly closed (network drop after creating the row, before fetching Q1), the user is **locked out** of starting a new session on the same topic until the timer expires. There is no `abandon_quiz` (it is referenced but not defined). **Recommendation**: define a sixth-tool-or-not decision. Options:

1. Add `abandon_quiz(session_id)` as a real tool — but this breaks GOV-010 ("five tools").
2. Allow `start_quiz` with an `abandon_existing: bool` flag — pollutes the contract but reuses the tool.
3. Sweeper auto-abandons sessions stuck in `Active` with `currentIndex == 0` after 5 minutes — cleanest, but adds latency to the "I want to restart NOW" path.

Recommended: **option 3** (sweeper) + a UX path where the agent tells the user "your previous session is being closed; please try again in a minute".

### 4.5 Dispatcher does not enforce tool allowlist or parallel-call rejection

Today `tasks/004 TASK-063` "registers exactly five tools" — but registration alone doesn't enforce GOV-010 if the MAF runtime accepts model-emitted tool names that don't match a registered tool. The defense GOV-010 promises is a **dispatcher that fails closed**. **Recommendation**: add a thin dispatcher in `quiz_agent.py` between the MAF turn loop and the tool implementations, with an explicit whitelist check + in-flight `(session_id, question_id)` mutex.

### 4.6 Question-bank → AI Search → Cosmos `topics` triple source of truth for counts

`topics.counts[language]` in Cosmos is set by the seed loader (or a reconciliation job — see `tasks/003 TASK-043` risk note). If the loader and reconciler can drift, **`list_topics`'s `count`/`has_fallback` will diverge from reality**. `tasks/003 TASK-043` explicitly defers reconciliation. **Recommendation**: either move count computation **out** of Cosmos and read from AI Search facet counts at request time (cached), or add a reconcile-after-reindex task to `tasks/002` (TASK-028 already does the reindex; extend to also update `topics.counts`).

### 4.7 Foundry Hosted Agent identity model — SAMI vs UAMI ambiguity

`tasks/001 TASK-003` says the Foundry project's **system-assigned identity is enabled**. `tasks/001 TASK-010` says the Hosted Agent gets a **UAMI attached**. `infra/README §3.1` says **UAMI only** (because SAMI rotates on recreate and breaks RBAC). **Recommendation**: drop the SAMI-enabling step from TASK-003 or document why both exist (e.g., SAMI for project-level audit while UAMI for workload). Otherwise role assignments may target the wrong principal silently.

### 4.8 Cosmos multi-region — single-region in v1 vs prod multi-region in infra spec

`tasks/001 TASK-004` says "single-region first" and defers multi-region to v2. `infra/README §1.1` says prod is **multi-region, zone-redundant, autoscale**. The infra spec is more recent and more enterprise-aware; the task is anchored to "PoC" phasing. **Recommendation**: for v1 in `dev`/`qa`, single-region is fine; for `prod`, follow the infra spec and provision multi-region with manual failover. Update TASK-004 to parameterize the region count per env.

---

## 5. Security Gaps

### 5.1 `expected` in telemetry — inconsistent treatment (P1)

| Doc | Allows `expected` (answer keys) to leave server? |
|-----|--------------------------------------------------|
| `008-api §4.5` GradingEvent | Yes — `expected: OptionKey[]; // 🟡 emitted only to App Insights / audit, NOT to LLM` |
| `tasks/008-observability TASK-141` | "Dimensions: ... `expected`, `received` ..." → emitted to App Insights |
| `infra/README §11.2 INF-101` | **Forbidden**: "`correct_answer` (any language)" cannot appear in any log |
| `testing-matrix.md AL-006` | "`audit.expected` is recorded server-side only — not emitted to telemetry that flows back to client" |

These disagree on whether App Insights is considered "server-only telemetry" (where `expected` is acceptable) or "any log" (where it is forbidden). **Recommendation**: pick one. The defensible answer is:

- **`expected` MAY appear in the `audit` Cosmos container** (it is the system of record for disputes, governed by RBAC + `SECT-009` retention).
- **`expected` MUST NOT appear in App Insights / Log Analytics** (these are broader-access surfaces with their own retention; emitting answer keys widens the trust boundary).

Update `008-api §4.5` and `tasks/008 TASK-141` to **omit** `expected` from the App Insights `grading_event` and only persist it to `audit`.

### 5.2 `receivedRaw` in `grading_event` — same kind of leak (P1)

`008-api §4.5` GradingEvent: `receivedRaw: string; // 🟢 PII; subject to retention`. But `tasks/008 TASK-141` explicitly says **"raw user utterance is NOT part of this event (transcripts have separate retention)"**.

**Recommendation**: follow TASK-141. Remove `receivedRaw` from `grading_event` in `008-api §4.5`. Keep `receivedRaw` only in the `audit` container (already specified there) and let App Insights see only the normalized `received` option key.

### 5.3 `agent.injection_detected` payload-hashing not implemented

`009-gov §7.2 GOV-061` says "log a hash of the offending utterance (not the raw text — that is PII)". `tasks/008-observability.md` does not implement this event at all, let alone its hashing rule. **Recommendation**: add to the new `agent.*` events task (§3.3 above).

### 5.4 Prompt-hash integrity gap (P1)

GOV-003 calls a prompt-hash mismatch mid-session "**P0 — halt session, page on-call**". But:

- No task computes the prompt hash.
- No task verifies it on every turn.
- No test (TEST-025 is referenced but not defined) covers it.

**Recommendation**: implement prompt-hash composition + per-turn verification in the new task (§3.3 above). The hash should be computed once at session start, written to the session row, and compared at every tool invocation. Mismatch → halt + alert (per infra §10.2 alert table).

### 5.5 RBAC: indexer needs `Search Service Contributor` but task pack underspecifies

`infra/README §4.2` says `uami-indexer-*` gets `Search Service Contributor` to **write the index**. `tasks/001 TASK-011` mentions `Search Index Data Contributor`. These are different roles:

- `Search Index Data Contributor` — data-plane write to existing indexes.
- `Search Service Contributor` — control-plane (create/delete indexes).

The seed loader (`tasks/002 TASK-026`) must be able to **create the index** (TASK-020 says it can be done via Bicep deployment script or via the Python loader). If the latter, `Search Service Contributor` is needed. **Recommendation**: decide who owns the index lifecycle. Cleanest: Bicep creates the index (data plane via deployment script with a deployer principal); the seed loader only writes documents (Data Contributor). Update both TASK-011 and TASK-027 accordingly.

### 5.6 Prompt-injection corpus coverage — no encoded-payload variant

`tasks/007 TASK-126` lists "base64", "ROT13", "leetspeak" in `009-gov §7.1` but the task only specifies plain English/French/Spanish prompts. **Recommendation**: extend the corpus to encoded payloads explicitly. The "expected response" should still be "agent declines without acknowledging" — and the test must assert the agent did **not** decode-and-execute.

### 5.7 GDPR right-to-erasure — no flow implemented (P1)

`infra/README §12.2 rule 4` describes a deletion cascade. No task in `tasks/` implements it. **Recommendation**: add a deletion-flow task to `tasks/007`. The flow must:

1. Mark `users.{userId}` as deleted (soft-delete with retention for legal hold).
2. Delete all `sessions` partitioned by that `userId`.
3. **Replace `userId` in `audit` rows with a pseudonym** (not delete — audits are evidentiary).
4. Log the deletion event to `audit` (the audit of the audit).

### 5.8 No protection against `userId` impersonation in tool args

`008-api §1.4` says `user_id` must match the authenticated principal. `tasks/005 TASK-082` does not call out the principal check explicitly. The risk: an attacker who can craft tool inputs (via prompt injection or compromised client) could call `set_language("klingon", user_id="victim")` — which is rejected on `language` but not on `user_id`. **Recommendation**: every tool must validate `user_id == authenticated_principal` at the dispatcher. Add an assertion task in `tasks/007` and a test in `tasks/009-testing`.

### 5.9 Voice biometrics out of scope — but no defense against replayed audio

SEC-012 explicitly defers voice biometrics. But a captured voice answer played back from another device could double-submit. The Cosmos `ifMatch` etag prevents the **double-score**, but not the **replay-and-impersonate**. This is fine for v1 (the user has authenticated the **session**, and Realtime tokens are per-session). **Recommendation**: document this explicitly in `specs/005 §10` so it is not mistaken for an oversight.

---

## 6. Implementation Risks

### 6.1 Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| R1 | Numeric defaults (C1–C5) ship in code mismatched with `008-api` examples → flaky tests, wrong TTLs in prod | High | High | Fix-1..Fix-6 before any code in `tasks/003`, `tasks/005`. |
| R2 | Field name drift (C7–C13) → Pydantic models, tool inputs, and Cosmos docs don't deserialize | High | High | Land the canonical names doc and write Pydantic aliases. Make `tests/test_no_answer_leakage.py` also assert exact field name presence. |
| R3 | Voice latency budget (250 ms p95 `submit_answer`) is tight given 2 Search + 1 Cosmos write | Medium | Medium | LAT-001 is the existing guard; tune AI Search replicas to absorb p95 spikes; verify under-load before voice GA. |
| R4 | `abandon_quiz` path undefined → users locked out of restarting a session | Medium | Medium | Implement the sweeper option (§4.4). |
| R5 | Prompt-hash integrity unimplemented but governance-critical (GOV-003) | Medium | High (governance breakdown if model drifts) | Add the task in §3.3 to the pre-public gate. |
| R6 | `expected` leaks into App Insights (§5.1) | Medium | High (SEC-001-class regression in telemetry) | Fix §5.1 before observability task lands. |
| R7 | GDPR cascade not implemented; legal/compliance gate cannot be passed | Low (until first request) | High | Add §3.3 deletion-flow task. |
| R8 | TEST-018..025 not defined → release pipeline cannot enforce GOV-* | High | Medium | §3.5 promotion of TEST IDs into spec 006. |
| R9 | Background sweeper unimplemented → sessions stuck `Active`, partition hot keys for one userId | Medium | Medium | §3.3 sweeper task. |
| R10 | Foundry Realtime regional availability differs from Cosmos region pair | Medium | Medium | Already flagged in `tasks/001 TASK-003`. Confirm region availability matrix on the chosen pair. |
| R11 | Cosmos multi-region in prod (per `infra/README`) raises consistency-model questions that v1 has not addressed (the spec is silent on read-region routing under failover) | Low (until failover) | Medium | Document the failover read path and update `specs/002 §11`. |
| R12 | Two-method search client naming drift (C10) → reviewers can't tell which method is "safe" by name alone — the core defense-in-depth weakens | Medium | High | Fix-10. |
| R13 | Topic count drift (`topics.counts` vs reality) → `list_topics` lies about availability | Medium | Low (UX bug) | §4.6 reconcile-after-reindex. |
| R14 | Dispatcher not implemented (§4.5) → MAF runtime drift could surface unregistered tools | Medium | High (governance breakdown) | §3.3 dispatcher task. |
| R15 | TTS shaper number expansion over-eager (mangles "10.0.0.0/8" if naively applied) | Low | Low | Already flagged in `tasks/005 TASK-087` risk. Ensure unit test UT-012 covers this. |

### 6.2 Pre-public risks

The pre-public gate (`tasks/010 TASK-189`) currently requires APIM, retention, and SEC-009 doc review. **Add**:

- TEST-025 (prompt-hash stability) green.
- TEST-023 (injection corpus) green across en/fr/es and encoded variants.
- TEST-018 (prompt redaction lint) green.
- GDPR deletion flow tested end-to-end.
- `expected` and `receivedRaw` confirmed absent from App Insights (post-Fix §5.1, §5.2).

---

## 7. Governance Risks

### 7.1 Behavioral contracts have no enforcement layer

Many GOV-* rules are presently **prompt-only contracts**. Per `009-gov §5.3 GOV-042`:

> The agent's behavioral rules are a layer; the tool boundary is the load-bearing layer.

This is correct as a *defense* statement, but the *operational* expression of GOV-* (events, alerts, blocks) is partially missing. Specifically:

- GOV-010 (tool allowlist) — needs dispatcher (§4.5)
- GOV-012 (no parallel `submit_answer`) — needs dispatcher mutex (§4.5)
- GOV-025 (coverage-gap consent) — needs flow change in TASK-089 (§3.3)
- GOV-091 (output token cap + truncation event) — needs implementation (§3.3)

If a GOV-* rule has no enforcing code and no test, **it is decorative**. The audit's recommendation is to treat the GOV-* IDs as **production requirements equal to FR/NFR/SEC** and gate them through the same release pipeline.

### 7.2 ADR coverage gaps

The five ADRs cover the top architectural decisions well. But several P1 decisions are made implicitly inside specs without an ADR:

- The two-tier state model (Cosmos vs AgentThread) — partially in ADR-003, but the **boundary between conversational thread and durable session** is more nuanced than the ADR captures.
- The exact retention windows (sessions / audit / transcripts) — change-control should require an ADR (per `infra/README §12.2 rule 5`); record one once C2/C3 are reconciled.
- The choice of **single record per `(logical_id, language)` pair** is in ADR-004, but the trade-off against the "single record with translations" approach deserves more rigor about per-language quality evaluation discipline (NFR-010).
- The choice of **deterministic Python grader vs LLM grader** is in ADR-005 indirectly. Worth an explicit ADR or a §-amendment.

**Recommendation**: add ADR-006 (Retention Policy) once C2/C3 are reconciled.

### 7.3 Specs ↔ tasks coupling is brittle

The current model is: `specs/` is the contract, `tasks/` is the work plan. As `008-api-contracts.md` and `009-agent-governance.md` were added, `003-data-contracts.md` and `004-agent-behavior.md` (the earlier docs) **were not updated** with the cross-references the newer docs need. The refactor-summary's "what was preserved verbatim" list shows the older spec is treated as immutable.

**Recommendation**: declare `008-api-contracts.md` and `009-agent-governance.md` as the **authoritative** sources for wire/protocol/behavior, and reduce `003-data-contracts.md` + `004-agent-behavior.md` to **summary documents** that point at the new docs. This eliminates the "two specs disagree on a default" class of bug structurally.

### 7.4 No spec covers the **operational handoff** of the question bank

Authors write to Blob; loader writes to AI Search; per-language Foundry Evaluation gates publishes. But:

- Who has write access to `authoring`?
- What is the review process for adding a language?
- Who runs the evaluation regressions and acts on them?
- How are failed evaluations rolled back?

These are operational governance issues; `tasks/002` and `tasks/009 TASK-167` describe pieces, but the end-to-end "content team workflow" is not written down. **Recommendation**: write `docs/content-governance.md` (or `specs/010-content-governance.md`) covering the author-publish-evaluate lifecycle.

### 7.5 Spec versioning is implicit

No spec carries an explicit version or "last reviewed" header (except ADRs, which carry a Date). When the team picks up this kit in 3 months, knowing which spec is the "current" one in light of newer additions will be unclear. **Recommendation**: add a `last_reviewed: YYYY-MM-DD` and `version: vN` header to every spec.

---

## 8. Priority Fixes

Ranked by **blast radius × probability × cost-to-fix-now-vs-later**.

### P0 — Fix before any code lands (next sprint)

1. **Fix-1..Fix-6** (numeric defaults). One-hour PR; unblocks `tasks/003`, `tasks/005`, `tasks/010` work.
2. **Fix-10** (canonical search-client method names). One-line edit + one-line code-review enforcement (007-security TASK-125 lint). Without this, ADR-005 defense weakens.
3. **§5.1, §5.2** (remove `expected` and `receivedRaw` from App Insights `grading_event`). Land before observability TASK-141 starts.
4. **§3.5 + §3.3** (promote TEST-018..TEST-025 to first-class TEST-* IDs and add the implementing tasks). Without these, the GOV-* contracts are unenforceable and the pre-public gate fails by definition.

### P1 — Fix within the Phase-2 window

5. **§3.3 dispatcher + parallel-call mutex** (GOV-010, GOV-012).
6. **§3.3 prompt-hash composition + verification** (GOV-003).
7. **§3.3 fallback-with-consent flow** (GOV-025).
8. **§3.3 background sweeper** (`Active → Paused → Expired` transitions).
9. **§4.4 `abandon_quiz` decision** — pick sweeper or flag.
10. **Fix-7..Fix-9, Fix-11..Fix-13** (field name + type drift).
11. **§3.4 author missing reference docs** (`docs/llm-boundary.md`, `docs/retention.md`, etc.).

### P2 — Fix before public exposure (Phase-3 gate)

12. **§5.7 GDPR right-to-erasure** flow + test.
13. **§3.3 agent.* observability events** + workbook entries.
14. **§4.5 dispatcher hardening** + test (TEST-019).
15. **§4.6 topic-counts reconciliation**.
16. **§4.7 SAMI/UAMI cleanup**.
17. **§5.5 RBAC role choice** (`Search Service Contributor` vs `Search Index Data Contributor`).
18. **§7.2 ADR-006 (Retention Policy)** once C2/C3 are resolved.
19. **§7.4 content governance doc**.
20. **§7.5 spec versioning headers**.

---

## 9. Implementation Order Improvements

The current `tasks/010 TASK-192` Phase 1 → 4 order is sound at the high level. Two refinements would tighten dependencies and reduce rework risk.

### 9.1 Front-load the security boundary tests

`tasks/009 TASK-160` (TEST-006 leak test) is currently scheduled implicitly with Phase 2. **Move to Phase 1**: write a leak-test skeleton against a stub tool layer before any tool implementation lands. The skeleton fails until the strip is in place; the strip lands in `tasks/005 TASK-088`. This locks the security contract before code accretes around it. (The refactor summary already recommended this in §7 step 4.)

### 9.2 Land the Pydantic model layer first

`tasks/003 TASK-045` (Pydantic models) is the choke-point for all downstream tools, tests, and contracts. Build it **before** the Cosmos repository, **before** the AI Search client, **before** the tools. Doing so:

- Forces the C7–C13 field-name reconciliation to be a code-level decision, not a doc-level one.
- Gives every test the right schema to assert against.
- Makes `tasks/005 TASK-080` (Tool I/O models) trivial.

### 9.3 Insert a "API contract finalization" step

Between Phase 1 (PoC core) and Phase 2 (Voice + hardening), add a one-day **freeze**:

1. Apply all P0 fixes from §8.
2. Pin `008-api-contracts.md` and `009-agent-governance.md` as v1.0.
3. Lock the Pydantic models against the pinned API contract.
4. Re-run `tests/test_no_answer_leakage.py` on the stub tool layer to verify the schema gate.

This freeze prevents the "every voice/hardening task has to re-litigate a contract decision" failure mode.

### 9.4 Dispatcher before tools

`tasks/004 TASK-063` (tool registration) + new dispatcher task should precede `tasks/005 TASK-081..085` (tool implementations). If the dispatcher exists from day one, tools are implemented against the dispatcher's behavioral contract (allowlist enforced, parallel-call mutex enforced) rather than against the raw MAF runtime. This avoids retrofitting governance into tools later.

### 9.5 Background sweeper before voice

The voice channel is **the flakiest network path** and the most likely to leave `Active` sessions stranded. Implement the sweeper as part of Phase 1 / early Phase 2, before voice traffic exercises the failure modes. Today's plan defers operational polish to Phase 3.

### 9.6 Per-language evaluator before seeding 3 languages

`tasks/009 TASK-167` (Foundry Evaluation per language) is scheduled in Phase 3. **Move forward**: evaluator harness in Phase 1 (against a 10-question seed in en/fr/es). This catches per-language quality drift on the smallest possible seed, when it's cheapest to fix. Otherwise the team will discover translation drift only after authoring 90 questions.

### 9.7 Recommended revised order (compact)

| Phase | Days | Key tasks |
|-------|------|-----------|
| **Phase 0 — Contract freeze** | 1 | Apply P0 fixes (§8). Pin `008-api` + `009-gov` v1.0. Promote TEST-018..025 into `006-testing-strategy`. |
| **Phase 1 — Foundations** | 3–4 | Bicep skeleton (`tasks/001`). Pydantic models (`tasks/003 TASK-045`). Two-method search client (`tasks/002 TASK-027`). Stub leak test (`tasks/009 TASK-160`). Tool dispatcher (new task) + tool registration (`tasks/004 TASK-063`). Five tools with TTS-friendly returns (`tasks/005 TASK-081..087`) and defensive strip (TASK-088). MAF agent skeleton (`tasks/004 TASK-061..062`). |
| **Phase 2 — Persistence + observability** | 3–4 | Cosmos containers (`tasks/003 TASK-040..044, TASK-050..051`). Conditional-write repository (`TASK-047`). State machine (`TASK-048`). Reproducible shuffle (`TASK-049`). Seed loader (`tasks/002 TASK-026..028`). Initial seed content (`TASK-025`). App Insights + grading_event (`tasks/008 TASK-140..141`). Tracing span discipline (`TASK-144`). Per-language evaluator harness (`tasks/009 TASK-167`). |
| **Phase 3 — Voice + hardening** | 3–4 | Realtime endpoint wiring (`tasks/006 TASK-100..103`). Voice answer normalization (`TASK-104`). Voice latency budget enforcement (`TASK-107`). Voice dashboard (`TASK-109`). Channel-switch tolerance (`tasks/004 TASK-068`). Resumption (`TASK-067`). Background sweeper (new task). Prompt-hash composition + verification (new task). Output-length cap + truncation event (new task). |
| **Phase 4 — Governance + ops polish** | 3 | Coverage-fallback consent flow (TASK-089 rewrite). `agent.*` observability events. Refusal localization tests. Injection corpus tests (en/fr/es/encoded). State-machine and timer tests. Audit retention + immutable archive. GDPR deletion flow. |
| **Phase 5 — Pre-public exposure** | 2 | APIM + per-user quotas. Pre-public gate CI. Retention applied. SEC-009 doc reviewed. All TEST-* + GOV-* tests green. |

This sequence delivers the same v1 deliverables (`tasks/010 TASK-192`) but front-loads the **contract-shape** and **dispatcher** decisions that are otherwise expensive to retrofit.

---

## 10. Operational Readiness Improvements

### 10.1 The metric that matters needs to be defined, not just observed

`specs/007 §2.2` and `tasks/008 TASK-143` both emphasize "grading correctness is the metric that matters, not uptime." Good. But **what is the SLO?**

- p95 of per-language correctness rate stays within ±X% of baseline over 7 days — what is X?
- Per-question correctness < Y% triggers an author review — what is Y?
- A new question must exceed Z% correctness in the first 100 answers — what is Z?

Without these numbers, the dashboards are decorative, the alert in `infra/README §10.2` ("Answer-leakage indicator (TEST-006 prod canary) — Sev 1 P0") is the only correctness alert with teeth.

**Recommendation**: add a `specs/011-slos.md` (or §-extend `007`) with explicit numeric SLOs for: voice tool-call p95, text tool-call p95, STT/TTS p95 per language (today TBD in `tests/specs/testing-matrix.md` LAT-004/005), correctness drift, error rates per tool, idempotency-violation count (must be **zero**).

### 10.2 Incident playbook needs the actual KQL queries

`tasks/008-observability TASK-148` says "Incident runbook hooks — attach the App Insights query that surfaces evidence." That's the right move — but no task delivers the queries themselves. Each runbook entry in `specs/007 §9` and `infra/README §10.2` needs the KQL written and committed.

**Recommendation**: add a `docs/incident-queries.md` with one named, parameterized KQL per runbook row. Pre-public gate enforces presence.

### 10.3 DR drill cadence is documented but un-tasked

`infra/README §9.4` says "A DR drill runs at least every 90 days (OPS-004)" — and "A failed drill blocks the next `prod` deploy until remediated." But there is no task in `tasks/` that defines or schedules the drill. **Recommendation**: add to `tasks/010` (or new `tasks/011-disaster-recovery.md`): drill playbook, schedule, success criteria, evidence file.

### 10.4 Cost ceiling vs autoscale ceiling reconciliation

`infra/README §7.3` budgets and `§8.1` autoscale ceilings (Cosmos `10000` RU/s, AI Search 2→4 replicas) don't have a single number that says "X $/month is the ceiling that would trigger paging at 100%." `tasks/010 TASK-191` mentions monthly budget alerts but no specific value. **Recommendation**: bake an `expectedMonthlyCost.{dev,qa,prod}` parameter into `infra/main.parameters.json` and assert in the post-deploy hook that the budget alert is set against it.

### 10.5 Voice-specific operational gaps

- **Per-language voice quality**: no test verifies that the TTS voice for each language is intelligible (subjective metric). Run a one-time human review per language at GA.
- **STT confidence floor**: `tasks/006 TASK-102` says "confidence > 0.7 in steady test conditions" but the action below 0.7 is not specified. If STT returns low-confidence, does the agent re-prompt? Today no path forces this; the normalizer may still return a match. **Recommendation**: route low-confidence STT outputs to a re-prompt before grading; add to `tasks/005 TASK-086` / `tasks/006 TASK-104`.

### 10.6 Multilingual operational maturity

The kit is mature on per-language testing (good ML-* suite) and per-language evaluation (NFR-010 / TEST-011). But operational gaps:

- **Author tooling**: no spec covers the tooling authors use to write questions (Markdown editor? VS Code? CLI?). Adding a language is "author + reindex" but the author experience is undocumented.
- **Reviewer flow**: no spec describes how a reviewer signs off on per-language translations before they enter the index.
- **Rollback per language**: rollback is currently "reindex from prior Blob version" — but rolling back **only** the French translations while keeping English current is not described.

**Recommendation**: write `docs/content-governance.md` (§7.4) covering all three.

### 10.7 Pre-public gate is necessary but not sufficient

`tasks/007 TASK-130` enforces APIM, retention, and SEC-009 doc. **Add to the gate**:

- DR drill within last 90 days passed.
- All P0 + P1 audit findings (§8) closed.
- TEST-018..TEST-025 + state-machine + timer tests green.
- `expected` / `receivedRaw` confirmed absent from App Insights (post §5.1 / §5.2 fixes).
- GDPR cascade tested end-to-end.
- Prompt-hash verification (GOV-003) active in prod.

### 10.8 On-call runbook does not cover content-team incidents

`specs/007 §9` covers infra/security incidents. But the most common production incident in an exam system is "Question 42 in French is wrong/ambiguous". The runbook does not cover:

- How a user disputes a verdict (the agent says: "your session is recorded — contact support with your session ID" per GOV-103).
- How support reads `audit` to triage.
- How the content team retracts a question and reindexes.
- How `audit` records survive the retraction (they must — disputes outlive content).

**Recommendation**: extend `specs/007 §9` with content-incident rows; pair with content-governance doc (§7.4).

---

## 11. Summary

The Flint Quiz spec kit has a strong skeleton: a clear architecture verdict, well-traced FR/NFR/SEC/TEST IDs, a defensible security thesis, mature ADRs, and a thorough testing matrix. The principal weaknesses are **layering artifacts** from late additions (`008-api`, `009-gov`, `infra/README`) that did not propagate back into the earlier docs and the task plans:

- **6 numeric defaults conflict** (pass threshold, session TTL, audit TTL, shuffle seed, per-question limit, voice idle).
- **7 field names drift** across `003-data`, `008-api`, `tasks/005`, `009-gov`.
- **GOV-001..GOV-104 are mostly unimplemented** — they exist as prompt contracts, not as enforced code paths with tests.
- **TEST-018..TEST-025 are referenced but undefined** — the pre-public gate cannot currently enforce the governance discipline the GOV-* rules promise.
- **`expected` and `receivedRaw` leak into App Insights telemetry** in the api-contracts spec, contradicting the infra spec and AL-006 test. Fixing the spec is one-line; not fixing it is a SEC-001-class regression in telemetry.
- **The dispatcher that GOV-010/GOV-012 requires is not implemented** — making those rules decorative until added.
- **The background sweeper is documented but un-tasked**; `abandon_quiz` is referenced but undefined.
- **Reference docs** (`docs/llm-boundary.md`, `docs/retention.md`, etc.) are scheduled but not written.

None of these are architecturally fatal. All are addressable in a one-week consolidation pass before code lands at scale. The §8 priority list and §9 revised implementation order would resolve the P0 risks in a single sprint and the P1 risks within Phase 2. The structural recommendation — declare `008-api-contracts.md` and `009-agent-governance.md` as the authoritative sources and reduce the earlier docs to summaries that point at them (§7.3) — would eliminate the entire "two specs disagree on a default" class of bug.

The security thesis is sound and the test surface is appropriately weighted toward the answer-leakage + idempotency contracts. With the corrections in §3 and §5, the kit is implementation-ready.
