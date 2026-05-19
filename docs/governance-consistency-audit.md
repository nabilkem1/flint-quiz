# Governance Consistency Audit

- **Version**: v1.1 (post-remediation)
- **Date**: 2026-05-17 (audit) / 2026-05-17 (remediation pass)
- **Auditor**: Platform Engineering
- **Scope**: `docs/coding-standards.md`, `docs/ai-agent-development-guidelines.md`, `specs/001`–`009`, `adr/001`–`006`
- **Status**: ✅ **All findings resolved** — see §9 Resolution Log and updated Appendix A for the disposition of each finding.

---

## 0. Method

Cross-read every contract claim in the two new `docs/*.md` standards documents against the authoritative artefacts:

- Specs `001` (product), `002` (architecture), `003` (data), `004` (agent behavior), `005` (security), `006` (testing), `007` (runbook), `008` (API), `009` (governance).
- ADRs `001`–`006`.
- The `tasks/*` packs referenced from the standards.

Checked for: architectural contradictions, security inconsistencies, undefined terminology, missing governance rules, conflicting implementation guidance, missing AI safety constraints, and aspirational rules without enforcement owners.

### 0.1 Severity Classification

| Tier | Marker | Meaning |
|------|--------|---------|
| P0   | 🔴 | Security/scoring-integrity breach. Block merge. |
| P1   | 🟠 | Material contract conflict that will cause implementation defects or operational ambiguity. Fix before any consumer reads the document as authoritative. |
| P2   | 🟡 | Naming/terminology drift, documentation rot, missing definitions. Fix in next review cycle. |
| P3   | 🔵 | Polish, future-friendly improvements. Track but defer. |

### 0.2 Summary

- **0** P0 findings — the answer-leakage boundary is consistent across every document. The load-bearing security property holds.
- **5** P1 findings — all in the two new `docs/*.md` files; all are correctable in one editing pass.
- **9** P2 findings — a mix of pre-existing spec drift and new terminology I introduced without anchoring.
- **5** P3 findings — missing future-friendly rules.

Most issues are documentation defects, not implementation hazards. The most urgent: §1.1 (`OptionKey` narrowing) and §1.4 (`start_quiz` idempotency contract), both originating in the new standards docs.

---

## 1. P1 Findings — Material Conflicts in the New Standards Docs

### 1.1 🟠 `OptionKey` narrowed from `A..Z` to `A..E`

**Where**:
- `docs/coding-standards.md` §6.3 — `OptionKey` defined as `Literal["A","B","C","D","E"]`.
- `docs/ai-agent-development-guidelines.md` §5.6 — `OptionKey validated against the closed Literal["A"..."E"] set`.

**Source of truth**: `specs/008-api-contracts.md` §0.2 — `OptionKey` is "single uppercase letter A..Z"; §4.1 confirms "single [A-Z]; in question's option set".

**Conflict**: Both new docs unilaterally narrow the contract from 26 valid keys to 5. The spec leaves the upper bound to the question's option set; a future six-option question would silently fail validation if implementation followed the standards docs literally.

**Risk**: Pydantic validator rejects valid bank content. Tests parametrized on `Literal["A".."E"]` pass while production rejects records.

**Recommended fix**:
- In `docs/coding-standards.md` §6.3 and `docs/ai-agent-development-guidelines.md` §5.6, replace `Literal["A".."E"]` with: `OptionKey = NewType("OptionKey", str)` with a `Field(pattern="^[A-Z]$")` validator, bounded at runtime by `len(question.options) <= 26`.
- Add a clarifying note: "Today's question bank uses A–D consistently; the contract permits A–Z. Add new keys only by authoring a question record with more options; the type permits it."

---

### 1.2 🟠 `ErrorEnvelope` shape disagrees with spec 008 §4.2.1

**Where**:
- `docs/coding-standards.md` §6.5: envelope fields `code, class_, message_user, correlation_id` — followed by `# NOTHING ELSE 🟡/🔴 EVER`.
- `docs/ai-agent-development-guidelines.md` §3.5: envelope fields `code, class, message_user, correlation_id`.

**Source of truth**: `specs/008-api-contracts.md` §4.2.1 — envelope fields are `code, message_user, message_dev?, trace_id?, retryable, retry_after_ms?, detail?`. The spec marks `message_dev` and `detail` as 🟡 (server-only) but explicitly permits them in the envelope ("not shown to LLM").

**Conflicts**:
1. The new docs ban 🟡 fields outright; the spec allows them in the envelope with the rule that the LLM only ever sees `message_user`.
2. The new docs introduce a `class` field that does not exist in the spec.
3. The new docs use `correlation_id`; the spec uses `trace_id` (§4.2.1 field) and references "trace_id for support correlation".
4. The new docs omit `retryable` and `retry_after_ms` — both first-class in spec 008 §4.6 and used by the retry contract.

**Risk**: If implementers follow the standards verbatim, the retry contract from spec 008 §4.6 (which keys off `retry_after_ms` and `retryable`) cannot be honored. The dispatcher loses information needed for SDK-level retry handling.

**Recommended fix**: Rewrite both envelope sections to mirror spec 008 §4.2.1 exactly:

```ts
type ToolError = {
  ok: false;
  error: {
    code: ErrorCode;            // 🟢 stable enum (spec 008 §4.2.2)
    message_user: string;       // 🟢 localized, TTS-shaped
    message_dev?: string;       // 🟡 server-only; for telemetry, never to LLM
    trace_id?: string;          // 🟢 support correlation
    retryable: boolean;         // 🟢
    retry_after_ms?: number;    // 🟢
    detail?: Record<string, unknown>;  // 🟡 server-only
  };
};
```

Then add a separate, explicit rule: *"The agent's rendering layer surfaces ONLY `error.message_user` to the LLM/user. The presence of 🟡 fields on the envelope does not relax SEC-001 — the rendering layer is the gatekeeper. `message_dev` and `detail` go to App Insights via `trace_id` correlation."*

---

### 1.3 🟠 `start_quiz` idempotency (`I-S`) semantics inverted

**Where**:
- `docs/coding-standards.md` §6.4: "`I-S` operations require the dispatcher to detect an in-flight session for the same user/topic and return the cached in-flight result."
- `docs/ai-agent-development-guidelines.md` §3.4: "`I-S` operations (`start_quiz`) MUST detect an in-flight session for the same user/topic and return its `session_id` rather than creating a duplicate."

**Source of truth**: `specs/008-api-contracts.md` §1.5.6: "A second `start_quiz` call within `time_limit_seconds` of an existing `Active` session for the same `(user_id, topic)` does NOT create a second session. **It returns** `{ "ok": false, "error": { "code": "E_SESSION_ACTIVE", ..., "active_session_id": "<existing-id>" } }`. The agent must offer the user to **resume the existing session**."

**Conflict**: My standards say "return success with the existing session_id" (replay semantics). The spec says "return an error envelope with `E_SESSION_ACTIVE` carrying the `active_session_id` in `detail`". These behaviors render very differently to the user — the spec forces an explicit resume conversation; my docs imply a transparent re-attach.

**Risk**: Implementer follows the standards, produces an `I-S` that silently re-attaches. The user thinks they started a fresh quiz; the session state shows their old answers. The agent's resume flow (rehydrate from Cosmos, restate current question) never fires.

**Recommended fix**:
- In both docs, restate `I-S` semantics as: "`I-S` operations (`start_quiz`) MUST return `ok: false, code: E_SESSION_ACTIVE` with `active_session_id` in `detail` when an active session for the same `(user_id, topic)` exists. The agent then surfaces the resume affordance in the active-language phrasing block per [`008-api §1.5.6`](../specs/008-api-contracts.md)."
- Cross-link the spec section so the canonical semantics are one click away.

---

### 1.4 🟠 `raw_answer` logging policy stricter than the spec

**Where**:
- `docs/coding-standards.md` §1.10: forbids "raw user utterances over 200 chars (truncate + hash)" in any log line.

**Source of truth**: `specs/008-api-contracts.md` §1.6.7: "`raw_answer` is logged at INFO level (it is user-supplied input), but is redacted in transcripts older than the retention window (SEC-008)."

**Conflict**: The spec permits full `raw_answer` at INFO; my standards mandate truncate+hash. This is not a security regression in the strict direction, but it creates a discrepancy where compliant code per one document violates the other. Worse, hashing breaks the dispute-resolution chain — `audit.receivedRaw` and the log line `raw_answer` need to be the same value for triage to work.

**Risk**: A dispute investigation reads `audit.receivedRaw = "la deuxième"` and the App Insights log shows `raw_answer = "<truncated:hash>"` — no join key, no triage.

**Recommended fix**: Replace the bullet in `docs/coding-standards.md` §1.10 with: "*Forbidden in any log line:* `correct_answer`, secrets, etag, connection strings. *Permitted at INFO with retention discipline (SEC-008):* `raw_answer` up to the spec 008 §4.1 length cap (512 chars). The same value flows to `audit.receivedRaw` (server-only, RBAC-restricted). PII redaction is enforced by App Insights retention policy, not by log-line truncation."

---

### 1.5 🟠 Session TTL contradicts itself within `docs/coding-standards.md`

**Where**:
- `docs/coding-standards.md` §4.2: "TTL is per-container, not per-item, unless retention divergence requires per-item."

**Source of truth**: ADR 006 + spec 008 §2.1: `sessions` container has `defaultTtl` null and per-item `ttl` is set on transition to `Scored`/`Expired` to 30 days. This is **per-item**, not per-container. `audit` has container `defaultTtl = 365` (per-container).

**Conflict**: The "per-container, not per-item" framing is exactly backwards for `sessions`. Operationally, the engineer reading §4.2 will set `defaultTtl: 30d` on the container, which would expire `Active` sessions mid-quiz.

**Recommended fix**: Replace §4.2 paragraph one with: "`sessions` uses per-item TTL set on transition to terminal state (`Scored`/`Expired`), not container-level default. `audit` uses container-level `defaultTtl` because every row's retention is uniform. The pattern: per-container TTL when retention is uniform; per-item TTL when retention is gated on a state transition."

---

## 2. P1 Findings — Pre-existing Spec Drift the New Docs Inherited

These were already present in the spec corpus; my new docs propagated or compounded them. Fix-up should be coordinated across spec + standards.

### 2.1 🟠 Session TTL: 30 days vs 90 days within spec 008

**Where in spec corpus**:
- `specs/008-api-contracts.md` §2.1: "default `null` (no expire); set to `2592000` (**30 days**) on transition to `Scored`/`Expired`".
- `specs/008-api-contracts.md` §4.3 state-machine table: `Completed/Expired → Scored: ifMatch(_etag); **set TTL to 90d**`.
- `specs/008-api-contracts.md` §4.3 mermaid diagram caption: `Scored → [*] : TTL-driven cleanup (90d default)`.
- ADR 006: 30 days, authoritative.

**Conflict**: Spec 008 contradicts itself across §2.1 vs §4.3. ADR 006 is the deciding authority (and resolves to 30 days).

**Risk**: An implementer reading the state-machine table will set 90 days; ADR 006's enforcement test (TASK-132) expects 30 days. Deploy will fail post-deploy assertion, or worse, drift goes undetected because nobody re-runs TASK-132 post-PR.

**Recommended fix**: Open a spec PR against `specs/008-api-contracts.md`:
- Edit §4.3 table row "`Completed/Expired → Scored`": change "set TTL to 90d" to "set per-item TTL per ADR 006 (30 days hot)".
- Edit §4.3 mermaid caption: replace "90d default" with "30d default per ADR 006".

Note: ADR 006 §1 already records this as a discovered audit defect — but the spec was never updated. This is the cleanup PR.

---

### 2.2 🟠 `QuestionView` field set inconsistent within spec 008

**Where in spec corpus**:
- `specs/008-api-contracts.md` §1.5.4: `QuestionView` fields = `question_id, text, options, difficulty`. (No `topic`, no `language`.)
- `specs/008-api-contracts.md` §3.3.1: `SELECT_FIELDS = ["id", "logical_id", "topic", "language", "text", "options", "difficulty"]`, then `return QuestionView(**doc)`.

**Conflict**: The pseudo-code constructs `QuestionView(**doc)` with seven fields where the type definition has four. The Pydantic model would either reject the extras (with `extra="forbid"`) or silently accept them — neither matches the §1.5.4 type definition.

**Risk**: Implementer follows §3.3.1 pseudo-code → adds `topic`/`language`/`logical_id` to `QuestionView` → contract widens silently → future field added to AI Search index is also "automatically" inherited because the pattern was set.

**Recommended fix**:
- Update §3.3.1 pseudo-code: keep the AI Search projection at the wider set (it's an analyzer-driven projection on the index side), but explicitly construct `QuestionView` with only the four fields: `QuestionView(question_id=doc["id"], text=doc["text"], options=doc["options"], difficulty=doc["difficulty"])`.
- OR widen §1.5.4 to match — but this requires re-validating that `topic`/`language` are safe to expose to the LLM. (They are 🟢, so it's safe — but the explicit choice should be made in the spec, not by pseudo-code drift.)

Either way, the standards docs reference `QuestionView` heavily; they should be re-read once §1.5.4 is final.

---

### 2.3 🟠 Tool-call timeouts contradict each other

**Where in spec corpus**:
- `specs/009-agent-governance.md` §2.6 (GOV-014): "Tool call: **2s text / 800ms voice**".
- `specs/008-api-contracts.md` §4.6: "Tool-call timeouts (agent → tool): one retry **after 500 ms (text) / 250 ms (voice)**".

**Conflict**: These describe different things in similar words — but the values disagree. Spec 009's "timeout" is the wall-clock budget before the tool call is abandoned; spec 008's "after" is the retry-delay threshold. Reader cannot tell from prose.

**Risk**: Implementer pins the timeout at 500 ms because they read spec 008 first; voice latency budget is violated in production for legitimate slow Search responses.

**Recommended fix**: Reconcile in spec 008 §4.6 by relabeling the row: "*Tool-call retry delay*: 500 ms (text) / 250 ms (voice); tool-call timeout per [`009-gov §2.6`](./009-agent-governance.md) (GOV-014): 2s text / 800ms voice."

---

## 3. P2 Findings — Terminology, Naming, and Documentation Drift

### 3.1 🟡 `correlation_id` vs `trace_id` naming inconsistency

**Where**:
- New docs use `correlation_id` (`docs/coding-standards.md` §8.2; `docs/ai-agent-development-guidelines.md` §9.4).
- Spec 008 §4.2.1 uses `trace_id`.

**Conflict**: Same concept, two names. The new docs never explain that these are aliases; the spec never uses `correlation_id`.

**Recommended fix**: Pick one — `correlation_id` is the more conventional OTel-leaning name and matches what the docs already say. Update spec 008 §4.2.1 field from `trace_id` to `correlation_id` (with a one-line note that it is the W3C `traceparent`-derived ID).

### 3.2 🟡 Refusal localization test ID covers `GOV-052` but the rule lives in `GOV-071`/`GOV-072`

**Where**:
- `specs/006-testing-strategy.md` TEST-021: "Refusal localization (GOV-052, GOV-072)".
- New docs propagate this conflation (`docs/ai-agent-development-guidelines.md` §10.3; `docs/coding-standards.md` §9.5).
- Actual rule: spec 009 GOV-071 specifies "Refusal Shape" (language from active phrasing block); GOV-052 is "Score Preview Prohibition". GOV-072 covers "Soft Decline vs Hard Refuse".

**Conflict**: GOV-052 is cited as if it governs refusal localization; it doesn't. The right pair is GOV-071 (language requirement) + GOV-072 (soft vs hard).

**Recommended fix**: Update spec 006 TEST-021 reference to `(GOV-071, GOV-072)`; propagate to both new docs.

### 3.3 🟡 Operational runbook §10 reference is stale (pre-existing)

**Where**:
- `specs/009-agent-governance.md` GOV-103: "the agent's only allowed response is to offer the audit trail path documented in [007-operational-runbook §10]".
- `specs/007-operational-runbook.md` §10 is "References" — a link list, not a dispute path. The incident handling lives in §9.

**Conflict**: Dead reference in a P1 behavioral spec.

**Recommended fix**: In `specs/009`, change the GOV-103 reference to `[007-operational-runbook §9](./007-operational-runbook.md)` (incidents table). Optionally add an explicit "Dispute Resolution" subsection §11 to the runbook so future references survive section renumbers.

### 3.4 🟡 `detectedLanguage` permitted outside the SEC-010 allowlist

**Where**:
- `specs/008-api-contracts.md` §2.2: "`detectedLanguage` may be any ISO 639-1 (we record what was detected even if not supported)".
- New docs assert all language codes validated against allowlist (SEC-010 wording: `docs/coding-standards.md` §6.3 "`LanguageCode` validator calls the ISO 639-1 allowlist (SEC-010 / TASK-123)").

**Conflict**: A `LanguageCode` validator that enforces the SEC-010 allowlist will reject `detectedLanguage` values for unsupported languages — but the spec specifically wants to record them for the fallback decision.

**Recommended fix**: In `docs/coding-standards.md` §6.3, distinguish two types:
- `SupportedLanguageCode` — validates against SEC-010 allowlist (used by `set_language`, `start_quiz`, `sessions.language`).
- `DetectedLanguageCode` — validates only as ISO 639-1 (any two-letter code), used by `users.detectedLanguage`.

### 3.5 🟡 Undefined terms used in standards docs

The following terms appear in `docs/ai-agent-development-guidelines.md` and/or `docs/coding-standards.md` without definition in any spec:

| Term | First use | Recommended fix |
|------|-----------|-----------------|
| "pause_threshold" | Used in spec 008 §4.3 transition table | Spec 008 should define it (cross-reference to TASK-191 sweeper config). |
| "channel adapter" | `docs/ai-agent-development-guidelines.md` §7 intro | Define in glossary: "the per-channel I/O surface (Foundry Playground for text, Foundry Realtime endpoint for voice) that adapts native protocol to the shared agent loop." |
| "audit-of-audit" | `docs/ai-agent-development-guidelines.md` Appendix B | Already defined in glossary — fine. |
| "sweeper" | spec 008 §1.5.6, spec 009 — defined in TASK-191 but not in any spec section | Add a one-paragraph spec §4.8 to spec 008 explaining the sweeper's scope, owner, and run cadence. |
| "stranded session" | `docs/ai-agent-development-guidelines.md` §6.4 | Define inline: "an `Active` session with `currentIndex == 0` and no `submit_answer` traffic for more than `voice:maxStrandedSeconds` (default 300 s)". |
| "hot path" / "cold path" | Used pervasively | One-line glossary entries: "hot path: code path invoked per-user-turn, bound by NFR-001's 300 ms p95 voice budget; cold path: anything else (evals, archive jobs, sweepers)." |

### 3.6 🟡 `class_` vs `class` in ErrorEnvelope (within the new docs)

**Where**:
- `docs/coding-standards.md` §6.5: field named `class_` (Python reserved-word escape).
- `docs/ai-agent-development-guidelines.md` §3.5: field named `class`.

**Conflict**: Two new docs disagree on a field name. (And the field shouldn't exist at all per finding 1.2.)

**Recommended fix**: Remove the `class`/`class_` field entirely when fixing finding 1.2. The spec 008 envelope has no such field.

### 3.7 🟡 `src/common/` folder is introduced by standards, not specs

**Where**:
- `docs/coding-standards.md` §12.1 — `src/common/` for `config, exceptions, logging_setup, clock, telemetry`.
- `specs/007-operational-runbook.md` §1 lists `src/agent/`, `src/data/`, `src/seed/` — no `src/common/`.

**Conflict**: The standards introduce a new top-level folder the operational runbook does not enumerate. Tasks reference modules inside it (clock injection, telemetry initialization) implicitly.

**Recommended fix**: Update `specs/007-operational-runbook.md` §1 to add `src/common/` row: "Cross-cutting modules (config, exceptions, logging_setup, clock, telemetry) — owned by Platform, no domain logic."

### 3.8 🟡 Exception hierarchy names introduced by standards, not specs

**Where**:
- `docs/coding-standards.md` §1.9 defines `FlintError`, `FlintValidationError`, `InvalidLanguageError`, `FlintAuthorizationError`, `FlintNotFoundError`, `SessionStateError`, `FlintConflictError`, `FlintUpstreamError`, `FlintConfigurationError`, `AnswerLeakageError`.
- Specs refer to behaviors (`E_AUTH_MISMATCH`, `E_SESSION_NOT_ACTIVE`) but never name the Python exceptions.

**Conflict**: Not a contradiction — but the standards have no anchoring authority for these names. A divergent set could appear in code without violating any spec.

**Recommended fix**: Add a §6.4 to `specs/008-api-contracts.md`: "Python exception hierarchy" — name each exception, map to error envelope `class` (validation/authz/not_found/conflict/upstream/internal), point to the standards doc for inheritance.

### 3.9 🟡 Output cap "600 tokens" appears in standards as if from spec — confirm

**Where**:
- `docs/ai-agent-development-guidelines.md` §4.6: "Output cap: 600 tokens per turn ([GOV-091](../specs/009-agent-governance.md))".
- spec 009 §10.2 (GOV-091): "Output length per turn is capped at 600 tokens."

**Check**: Aligned. Documenting here only to note that I verified the citation; no defect.

---

## 4. P3 Findings — Missing Future-Friendly Rules

### 4.1 🔵 No GOV rule pinning the model deployment

The model deployment name lives in App Configuration (`model:deployment`) and is changed by hand. There is no GOV rule equivalent to GOV-003 (prompt hash) for the model — a silent model swap is possible without test re-runs. The standards reference an 8-step model upgrade process; the spec corpus has no equivalent.

**Recommended fix**: Add `GOV-150 — Model Pinning`: model deployment name is content-addressed in App Configuration; a change requires the §11.1 upgrade process from `docs/ai-agent-development-guidelines.md`; a change without a passing canary fails the pre-release gate (TASK-130).

### 4.2 🔵 No governance for App Configuration changes

`languages:supported`, `voice:maxStrandedSeconds`, `retention:*`, `model:deployment`, `voices:{lang}`, `features:*` all sit in App Configuration with runtime polling reload. There is no `GOV-*` rule on who may change them or what review is required.

**Recommended fix**: Add `GOV-160 — Runtime Configuration Discipline`: every App Configuration key has an owner team (CODEOWNERS-style), a change-control review, and a documented blast radius. Changes deploy via Bicep, not console edits.

### 4.3 🔵 No spec for prompt-cache invalidation on layer changes

When a prompt layer file changes (new SHA-256), in-flight sessions either (a) continue on the old composed hash (the standards' implicit answer) or (b) are halted by GOV-003. The standards say "running sessions complete on old hash"; GOV-003 says any mid-session mismatch is P0. These can both be true if the runtime keeps the old layer content cached for sessions pinned to the old hash — but no spec says so.

**Recommended fix**: Add a paragraph to spec 009 §1.2 (GOV-003): "On layer-file change, the deployment pipeline keeps the prior layer bytes resolvable by hash for the maximum session window (`time_limit_seconds` default 3600). Sessions started before the deploy continue on their pinned hash; sessions started after use the new hash. A hash-mismatch P0 fires only on tampering, never on natural cutover."

### 4.4 🔵 Native-speaker review process is aspirational

`docs/ai-agent-development-guidelines.md` §11.5 requires native-speaker sign-off on per-language phrasing-block changes. No spec, no task, no CODEOWNERS entry implements this.

**Recommended fix**: Either implement (CODEOWNERS entry `/src/agent/prompts/lang/fr.yaml @speakers-fr`) or downgrade to "SHOULD have native-speaker review where available". As-written, it is a rule no one owns.

### 4.5 🔵 Missing AI safety constraints in the standards docs

The following are absent or under-specified in `docs/ai-agent-development-guidelines.md` but matter for production:

| Gap | Recommendation |
|-----|----------------|
| Multi-correct question support | Spec 008 §5.4 normalizer signature has `accept_multi: bool`. Standards never address how the agent renders or handles multi-correct UX. Add to §8 (multilingual) or a new §7.6. |
| STT confidence handling | Tasks/006 TASK-102 says "confidence > 0.7" — but the agent's behavior on low-confidence is unspecified. Add to §7.4: "On STT confidence < 0.7, agent re-prompts in active language using the phrasing block's `LowConfidenceReprompt` line." |
| Model unavailability | If the Foundry model endpoint returns 5xx, the agent loop must degrade gracefully. Add to §11.4: "Model 5xx → session frozen, persist state, return localized `E_BACKEND_TRANSIENT`; do not retry in-process." |
| Tool circuit-break agent behavior | Spec 008 §1.5.9 defines `E_SEARCH_DEGRADED`+`session_frozen` but agent rendering isn't specified. Add to §11.4. |
| Voice barge-in vs cough/silence | The interruption rule covers user-speech detection; silence-vs-noise distinction (loud breathing, microphone bumps) isn't addressed. Tasks/006 references but doesn't specify the threshold. Add a §7.4 paragraph: "STT confidence + voice activity detection are both required to count as 'speech' for barge-in. A burst below either threshold is silence." |

---

## 5. Missing Standards (gaps not currently covered)

These standards are absent across the entire corpus (specs + ADRs + new docs):

| # | Missing standard | Why it matters |
|---|------------------|----------------|
| M1 | **Time-zone discipline.** All `ISO8601` is UTC by convention; no spec states it. Voice latency calculations assume monotonic clocks; no spec states which (server `time.time()` vs OTel-derived span duration). | Cross-region deploy, leap-second handling, latency-metric drift. |
| M2 | **PII redaction policy for App Insights query results.** App Insights is "broad-access" telemetry; queries return raw `userId`. Today's `userId` is an opaque Entra OID, but if it becomes a numeric internal ID in v2, queries leak PII. | Compliance regression on a future identity migration. |
| M3 | **Cosmos `_etag` lifetime.** `_etag` is a 🔴 token and never returned to the LLM. But standards don't say whether it can be logged with the redact-on-egress policy. | A log line `{"etag": "..."}` is per-call invariant data — useful for triage, sensitive to leak. |
| M4 | **AppConfig key TTL semantics.** Tasks/007 TASK-123 specifies "short-TTL cache" for the language allowlist without pinning a number. Same for `topics` cache, `model:deployment`. Coding-standards §1.12 mentions polling reload without a window. | Drift between consumers; race conditions on language additions. |
| M5 | **`schemaVersion` migration path** (coding-standards §6.2 references `src/data/migrations/`). No spec defines the migrator interface, the dual-write window, or the rollback path. | An incomplete migration leaves the read path branching forever. |
| M6 | **AST lint tooling.** Tasks reference `import-linter` and "AST lint" for the `get_answer_key` import restriction. No task names the tool, the CI step, or the exception path for refactors that legitimately move the symbol. | Lint silently disabled by a future renamer; SEC-001 enforcement degrades to TEST-006 alone. |
| M7 | **Pre-commit / commit-hook spec.** Coding-standards §1.4 references pre-commit but no `.pre-commit-config.yaml` spec exists. | Pre-commit drift; some contributors run it, others don't. |
| M8 | **Per-language synonyms-map versioning.** ADR 004 says synonyms maps are per-language; tasks/002 references them; no spec defines who owns them or how updates flow. | Synonym edit collides with a reindex flip; user sees a partial result set during the gap. |
| M9 | **"Tool result rendering" layer is mentioned but undefined.** `docs/ai-agent-development-guidelines.md` §2.5 says "The renderer is a thin shaper, not a co-author." No spec defines the renderer's contract (input shape, output shape, where it sits in the agent loop). | Two implementations diverge: one shapes per-tool, one shapes at the channel adapter. |
| M10 | **Eval result archival.** Per-language Foundry Evaluations (NFR-010 / TEST-011) produce scored results that gate publishes; no spec defines retention of those results or the appeal process when a publish is gated. | A blocked publish has no audit trail of why; content team cannot prove the regression. |

---

## 6. Implementation Risks

Risks created or exacerbated by the new standards docs as currently written:

### 6.1 🟠 Aspirational rules without enforcement owners

The new docs introduce several rules that have no spec backing, no task implementing them, and no CI step enforcing them:

- Native-speaker review (§11.5 of `ai-agent-development-guidelines.md`).
- 85% / 95% / 100% per-module coverage floors (§9.1 of `coding-standards.md`) — `pyproject.toml` and CI step do not exist yet.
- "Files over 600 lines need a refactor PR next sprint" (§12.3 of `coding-standards.md`) — no tracking.
- Pre-commit conventional-commits validation (§10.2) — no `commitlint` config in repo.
- AI provenance declaration in PR descriptions (§11.4) — no PR template enforces it.

**Risk**: Standards read authoritative on paper, are unenforced in practice. Drift accumulates silently until an incident.

**Recommended fix**: For each rule above, either (a) open the implementation task and link it from the standards doc, or (b) downgrade the language to SHOULD with an explicit "implementation tracked in TASK-NNN" footnote.

### 6.2 🟠 Folder layout vs operational runbook

`docs/coding-standards.md` §12.1 introduces `src/agent/composition.py`, `src/agent/tts_shaper.py`, `src/agent/prompts/`, `src/common/`, `src/data/erasure.py` — all justified by tasks but not enumerated in `specs/007-operational-runbook.md` §1.

**Risk**: A new engineer reads spec 007 and concludes some files are out-of-scope; codeowners list lags.

**Recommended fix**: Update `specs/007-operational-runbook.md` §1 to enumerate the full v1 layout exactly as in `docs/coding-standards.md` §12.1.

### 6.3 🟠 Two competing handbooks could drift

Both new docs cover overlapping ground (telemetry conventions, error envelope, idempotency, security boundary, multilingual rules). When they disagree (per findings §1.2, §3.6), reviewers can't tell which is authoritative.

**Risk**: Long-term content drift; reviewers cite whichever supports the change in front of them.

**Recommended fix**: Add a §0.1 to each doc stating: "Where this document and `<sibling-doc>` disagree, [name the winner] wins. Open a PR to reconcile." Suggested winner:
- For Python/repo conventions → `coding-standards.md`.
- For agent-loop/AI-policy → `ai-agent-development-guidelines.md`.
- For both: the corresponding spec/ADR wins over either.

### 6.4 🔵 ErrorEnvelope rendering is the single point of SEC-001 in error paths

Once finding 1.2 is fixed to allow 🟡 `message_dev` and `detail` on the envelope, the renderer that picks `message_user` to surface becomes the sole enforcement point for the LLM-boundary on errors. Today the renderer is undefined (see missing standard M9).

**Risk**: A logging mistake `logger.info(f"Tool error: {error}")` dumps the 🟡 fields into App Insights.

**Recommended fix**: Spec the renderer in a new spec 008 §6.4 ("Error Envelope Rendering Layer"). Specify the call site, the allowlist of fields surfaced to LLM context, and the lint check that forbids `{error}` interpolation in log lines.

### 6.5 🔵 The "two-sink" telemetry contract is fragile

`grading_event` (App Insights) and `audit` (Cosmos) are written from the same code path with different shapes. Today TEST-010 asserts `expected`/`receivedRaw` are absent from App Insights; the symmetric assertion (that they are present in `audit`) is not in the standards or test list.

**Risk**: A refactor that removes `expected` from `audit` writes goes undetected; the audit-of-truth degrades silently until a dispute can't be triaged.

**Recommended fix**: Add a test `tests/test_audit_completeness.py` (new TEST-029): on every persisted answer, assert the `audit` row contains all required fields including `expected` and `receivedRaw`. Add to `specs/006-testing-strategy.md` §2 table.

---

## 7. Recommended Fix Order

Sequenced to minimize churn:

1. **Pin the disagreements with spec 008** (findings 1.1, 1.2, 1.3, 1.4, 1.5) — edits to the new docs only; spec is unchanged. One PR.
2. **Reconcile spec 008 internal contradictions** (findings 2.1, 2.2, 2.3) — spec PR; coordinate with ADR-006 enforcement test (TASK-132).
3. **Fix dead/inverted references** (3.3, 3.6) — small spec + doc PR.
4. **Add missing definitions** (3.5) — append to glossaries.
5. **Surface missing standards** (M1–M10) — file as task tickets, prioritized by missing standard impact (M3 Cosmos etag logging and M6 AST lint are highest-leverage).
6. **Backfill enforcement for aspirational rules** (6.1) — open tasks; do not add more SHOULD/MUST until existing ones have CI.
7. **Spec the renderer** (6.4) — one focused spec PR; unlocks finding 1.2.
8. **Add `test_audit_completeness`** (6.5) — paired with TEST-029 in spec 006.

---

## 8. What Did NOT Break

The audit found that the load-bearing security and architectural properties are consistent across every document:

- **Tool boundary**: `correct_answer` is structurally absent from LLM context — ADR 005, SEC-001/002, GOV-005, TEST-006, the two-method search split in spec 008 §3.3, and both new docs all agree.
- **Idempotency**: Cosmos `ifMatch` on `(session_id, question_id)` is consistent across NFR-002, SEC-006, ADR 003, spec 008 §1.6.5, TEST-007, and both new docs.
- **Single-agent architecture**: ADR 002 + spec 002 §1 + spec 004 §1 + GOV-010 + both new docs all converge on "one agent, five tools".
- **State authority**: Cosmos is the system of record; threads are ephemeral. ADR 003 + spec 002 §7 + GOV-080/083 + both new docs converge.
- **Multilingual one-record-per-language**: NFR-011 + ADR 004 + spec 003 §2.1 + spec 008 §3.2 + both new docs converge.
- **Voice = same agent, same tools**: ADR 002 + spec 002 §6 + spec 004 §8 + GOV-050 + tasks/006 + both new docs converge.
- **Per-language Foundry Evaluations gate publishes**: NFR-010 + GOV-026 + tasks/008/009 + both new docs converge.
- **GDPR right-to-erasure cascade**: ADR 006 + SEC-008 + TASK-134 + TEST-028 + both new docs converge.
- **No `abandon_quiz` tool**: spec 008 §1.5.6 + GOV-010 + spec 009 §2.2 + both new docs converge.
- **Two-sink grading telemetry (App Insights 🟢 / Cosmos audit 🟡)**: NFR-009 + spec 005 §2 + spec 008 §4.5 + tasks/008 TASK-141 + both new docs converge.

The platform's hardest properties — answer leakage, idempotency, single-agent discipline, state authority — survived the audit without drift. The findings above are all on the documentation surface, not on the load-bearing semantics.

---

## 9. Resolution Log (2026-05-17 remediation pass)

All findings from §1–§6 were addressed in a single documentation-only remediation pass. No source code was touched; new tasks (TASK-TBD-COV1, TASK-TBD-COMMIT1, TASK-TBD-PRTPL1, TASK-TBD-CODEOWN1) were filed for the unwired enforcement gates surfaced by Risk 6.1 / Risk 4.4.

### 9.1 What changed in each file

| File | Changes |
|------|---------|
| `docs/coding-standards.md` | (§0.1 new) Document precedence rule. (§1.2) `OptionKey` widened to `^[A-Z]$` per spec, distinguish `SupportedLanguageCode` vs `DetectedLanguageCode`. (§1.10) `raw_answer` logging policy aligned with spec 008 §1.6.7; added explicit etag logging ban. (§1.12 new) Time-zone & clock discipline. (§4.2) TTL framing rewritten — per-item for `sessions`, per-container for `audit`. (§6.3) `OptionKey` widened. (§6.4) `start_quiz` `I-S` semantics corrected to `E_SESSION_ACTIVE` error envelope. (§6.5) `ErrorEnvelope` rewritten to match spec 008 §4.2.1 exactly + renderer reference. (§7.6 new) App Insights query PII discipline (covers M2). (§9.1) Coverage floors downgraded to SHOULD + TASK pointer. (§10.2) Conventional commits enforcement marked SHOULD. (§10.3) AI provenance marked SHOULD. (§12.3) 600-line rule marked SHOULD. (Appendix A) AST-lint tool name anchored. |
| `docs/ai-agent-development-guidelines.md` | (§0.1 new) Document precedence rule. (§3.2) `OptionKey` widened, validation classes named. (§3.4) `I-S` semantics corrected. (§3.5) `ErrorEnvelope` rewritten to spec 008 §4.2.1 exactly. (§3.6 new) Multi-correct question handling. (§5.6) Validation rules updated. (§7.5–§7.7 new) STT confidence handling, model 5xx / circuit-break agent behavior, voice barge-in vs noise (covers Risk 4.5). (§11.5 / §11.6) Native-speaker review marked SHOULD with TASK pointer. |
| `specs/006-testing-strategy.md` | (TEST-021) Refusal localization references corrected to GOV-071, GOV-072. (TEST-029 new) Audit-completeness symmetric to TEST-010 (covers Risk 6.5). (TEST-030 new) Renderer test (covers Risk 6.4). (§2 table) Two new test-file rows. |
| `specs/007-operational-runbook.md` | (§1) Source layout expanded: `src/agent/composition.py`, `src/agent/tts_shaper.py`, `src/agent/renderer.py`, `src/agent/prompts/`, `src/common/`, `src/data/keyvault_client.py`, `src/data/erasure.py`, `src/data/migrations/`, `.pre-commit-config.yaml` (covers M7 + Risk 6.2 + 3.7). (§10 new) Glossary — hot/cold path, sweeper, stranded session, pauseThresholdSeconds, channel adapter, renderer, audit-of-audit (covers 3.5). (§11 new) Dispute resolution path (target of GOV-103). (§12) References renumbered. |
| `specs/008-api-contracts.md` | (§3.3.1) `get_question_view` pseudocode now constructs `QuestionView` explicitly with the four-field allowlist (covers 2.2). (§4.2.1) `trace_id` renamed to `correlation_id` (covers 3.1). (§4.3 mermaid + table) `90d` → `30d` TTL per ADR 006 (covers 2.1); `pause_threshold` named as `sessions:pauseThresholdSeconds`. (§4.6) Tool-call retry-delay vs timeout disambiguated, cross-linked to GOV-014 (covers 2.3). (§6.3 new) Python exception hierarchy named and mapped to error codes (covers 3.8). (§6.4 new) Error envelope rendering layer specified (covers Risk 6.4 + M9). (§6.5 new) `schemaVersion` migrator interface specified (covers M5). (§6.6) Cross-reference renumbered. (§6.1) Example envelope updated to `correlation_id`. |
| `specs/009-agent-governance.md` | (§1.2) GOV-003 prompt-cache cutover discipline added (covers 4.3). (§11.4 GOV-103) Runbook reference updated to §11 (covers 3.3). (§17 new) GOV-150 Model Pinning + GOV-160 Runtime Configuration Discipline (covers 4.1 + 4.2 + M4). (§18) Testability renumbered. |
| `adr/004-use-ai-search-for-question-bank.md` | New "Synonyms-Map Versioning and Ownership" section (covers M8). |
| `adr/006-retention-policy.md` | New "Foundry Evaluation result archival" section (covers M10). |
| **New tasks filed** | TASK-TBD-COV1 (pytest-cov threshold + CI gating), TASK-TBD-COMMIT1 (commitlint pre-commit + CI), TASK-TBD-PRTPL1 (PR template with AI provenance field), TASK-TBD-CODEOWN1 (CODEOWNERS for per-language phrasing blocks). |

### 9.2 Outstanding work (tracked, not blocking documentation)

These are enforcement gates the audit recommended SHOULD be wired; they are downgraded to SHOULD in the docs and tracked:

- **TASK-TBD-COV1** — pytest-cov threshold + CI gating for §9.1 coverage floors.
- **TASK-TBD-COMMIT1** — `commitlint` in `.pre-commit-config.yaml` + CI.
- **TASK-TBD-PRTPL1** — PR template with the AI provenance field.
- **TASK-TBD-CODEOWN1** — CODEOWNERS entries for `/src/agent/prompts/lang/*.yaml`.

A follow-up audit after these tasks land will re-promote the affected rules from SHOULD back to MUST.

---

## Appendix A — Findings Index (with resolution status)

✅ = closed in 2026-05-17 remediation pass · ⏳ = closed-deferred (downgraded to SHOULD, tracked via TASK-TBD-*).

| # | Severity | Area | Title | Status | Fix location |
|---|----------|------|-------|--------|--------------|
| 1.1 | 🟠 P1 | Standards docs | `OptionKey` narrowed from A..Z to A..E | ✅ | `coding-standards.md` §6.3; `ai-agent-development-guidelines.md` §3.2, §5.6 |
| 1.2 | 🟠 P1 | Standards docs | `ErrorEnvelope` shape disagrees with spec 008 §4.2.1 | ✅ | `coding-standards.md` §6.5; `ai-agent-development-guidelines.md` §3.5 |
| 1.3 | 🟠 P1 | Standards docs | `start_quiz` `I-S` semantics inverted (replay vs error) | ✅ | `coding-standards.md` §6.4; `ai-agent-development-guidelines.md` §3.4 |
| 1.4 | 🟠 P1 | Standards docs | `raw_answer` logging policy stricter than spec | ✅ | `coding-standards.md` §1.10 |
| 1.5 | 🟠 P1 | Standards docs | Session TTL framed as per-container; spec says per-item | ✅ | `coding-standards.md` §4.2 |
| 2.1 | 🟠 P1 | Spec 008 | Session TTL: 30 vs 90 days within the same doc | ✅ | `specs/008-api-contracts.md` §4.3 (mermaid + table) |
| 2.2 | 🟠 P1 | Spec 008 | `QuestionView` field set vs `selected_fields` mismatch | ✅ | `specs/008-api-contracts.md` §3.3.1 |
| 2.3 | 🟠 P1 | Spec 008/009 | Tool-call timeouts contradict (2s vs 500ms text) | ✅ | `specs/008-api-contracts.md` §4.6 |
| 3.1 | 🟡 P2 | Specs + standards | `correlation_id` vs `trace_id` naming | ✅ | `specs/008-api-contracts.md` §4.2.1 (renamed) |
| 3.2 | 🟡 P2 | Spec 006 + standards | TEST-021 cites GOV-052; rule is GOV-071/072 | ✅ | `specs/006-testing-strategy.md` §1 + §2 tables |
| 3.3 | 🟡 P2 | Spec 009 | GOV-103 references non-existent runbook §10 | ✅ | `specs/009-agent-governance.md` §11.4; `specs/007-operational-runbook.md` new §11 |
| 3.4 | 🟡 P2 | Standards | `detectedLanguage` validation type missing | ✅ | `coding-standards.md` §6.3 (`SupportedLanguageCode` vs `DetectedLanguageCode`) |
| 3.5 | 🟡 P2 | Standards | Undefined terms (pause_threshold, sweeper, etc.) | ✅ | `specs/007-operational-runbook.md` new §10 Glossary |
| 3.6 | 🟡 P2 | Standards | `class_` vs `class` field name disagreement in new docs | ✅ | Field removed from envelope per fix 1.2 |
| 3.7 | 🟡 P2 | Spec 007 vs standards | `src/common/` introduced by standards, not in runbook | ✅ | `specs/007-operational-runbook.md` §1 |
| 3.8 | 🟡 P2 | Spec 008 vs standards | Exception hierarchy named only in standards | ✅ | `specs/008-api-contracts.md` new §6.3 |
| 3.9 | 🟡 P2 | Standards | Output cap citation verified — no defect | ✅ | No change needed (verified) |
| 4.1 | 🔵 P3 | Standards | No GOV rule pinning model deployment | ✅ | `specs/009-agent-governance.md` new §17.1 (GOV-150) |
| 4.2 | 🔵 P3 | Specs | No AppConfig change governance | ✅ | `specs/009-agent-governance.md` new §17.2 (GOV-160) |
| 4.3 | 🔵 P3 | Spec 009 | Prompt-cache invalidation on layer change underspecified | ✅ | `specs/009-agent-governance.md` §1.2 GOV-003 cutover paragraph |
| 4.4 | 🔵 P3 | Standards | Native-speaker review process aspirational | ⏳ | `ai-agent-development-guidelines.md` §11.5/§11.6/§14.7 downgraded to SHOULD; TASK-TBD-CODEOWN1 |
| 4.5 | 🔵 P3 | Standards | Multi-correct, STT-confidence, model 5xx, circuit-break, voice barge-in | ✅ | `ai-agent-development-guidelines.md` new §3.6, §7.5, §7.6, §7.7 |
| M1 | mixed | Standards | Time-zone discipline | ✅ | `coding-standards.md` new §1.12 |
| M2 | mixed | Standards | PII redaction policy for App Insights queries | ✅ | `coding-standards.md` new §7.6 |
| M3 | mixed | Standards | Etag logging policy | ✅ | `coding-standards.md` §1.10 (added explicit ban) |
| M4 | mixed | Specs | AppConfig key TTL semantics | ✅ | `specs/009-agent-governance.md` §17.2 (`appconfig:pollIntervalSeconds`) |
| M5 | mixed | Spec 008 | `schemaVersion` migrator interface | ✅ | `specs/008-api-contracts.md` new §6.5 |
| M6 | mixed | Standards | AST lint tool naming | ✅ | `coding-standards.md` Appendix A (named `tools/lint/check_answer_key_import.py`) |
| M7 | mixed | Spec 007 | Pre-commit / commit-hook spec | ✅ | `specs/007-operational-runbook.md` §1 (added `.pre-commit-config.yaml` row) |
| M8 | mixed | ADR 004 | Per-language synonyms-map versioning | ✅ | `adr/004-use-ai-search-for-question-bank.md` new section |
| M9 | mixed | Spec 008 | Renderer spec | ✅ | `specs/008-api-contracts.md` new §6.4 |
| M10 | mixed | ADR 006 | Eval result archival | ✅ | `adr/006-retention-policy.md` new section |
| 6.1 | 🟠 P1 | Standards | Aspirational rules without enforcement owners | ⏳ | `coding-standards.md` §9.1/§10.2/§10.3/§12.3 downgraded; TASK-TBD-COV1, COMMIT1, PRTPL1 |
| 6.2 | 🟠 P1 | Spec 007 | Folder layout mismatch with runbook | ✅ | `specs/007-operational-runbook.md` §1 (full layout enumerated) |
| 6.3 | 🟠 P1 | Standards | Two handbooks could drift | ✅ | Both standards docs new §0.1 (precedence rule) |
| 6.4 | 🔵 P3 | Spec 008 | Error rendering layer underspecified | ✅ | `specs/008-api-contracts.md` new §6.4 + TEST-030 |
| 6.5 | 🔵 P3 | Spec 006 | Two-sink contract lacks audit-completeness test | ✅ | `specs/006-testing-strategy.md` new TEST-029 |

## Appendix B — Documents Reviewed

- `docs/coding-standards.md` (v1.0, this review)
- `docs/ai-agent-development-guidelines.md` (v1.0, this review)
- `specs/001-product-requirements.md` (v1.0)
- `specs/002-system-architecture.md` (v1.0)
- `specs/003-data-contracts.md` (v1.0, superseded by 008 for wire-level)
- `specs/004-agent-behavior.md` (v1.0, superseded by 009 for behavioral contracts)
- `specs/005-security-model.md` (v1.0)
- `specs/006-testing-strategy.md` (v1.1)
- `specs/007-operational-runbook.md` (v1.0)
- `specs/008-api-contracts.md` (v1.0)
- `specs/009-agent-governance.md` (v1.0)
- `adr/001-use-microsoft-agent-framework.md`
- `adr/002-single-agent-architecture.md`
- `adr/003-use-cosmos-db-for-session-state.md`
- `adr/004-use-ai-search-for-question-bank.md`
- `adr/005-tool-boundary-prevents-answer-leakage.md`
- `adr/006-retention-policy.md`

Not exhaustively re-read: `tasks/*.md`, `docs/llm-boundary.md`, `docs/retention.md`, `docs/rollback.md`, `docs/secrets.md`, `docs/pre-public-gate.md`, `docs/content-governance.md`, `docs/playground.md`, `docs/refactor-summary.md`, `docs/spec-audit-report.md`. Sampled where new docs cited them.
