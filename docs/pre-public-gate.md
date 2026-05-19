# Pre-Public-Exposure Gate

**Purpose**: The checklist that **must** be green before any release tag is marked `public-ready` and any traffic from outside the trusted-tenant perimeter reaches the agent. Failing any item is a hard block.

**Owner**: Security + Release.
**Enforced by**: CI job (`tasks/007-security.md` TASK-130), `tasks/010-deployment.md` TASK-209.
**Cross-references**: [`specs/005-security-model.md`](../specs/005-security-model.md), [`specs/007-operational-runbook.md §8.4`](../specs/007-operational-runbook.md), [`docs/llm-boundary.md`](./llm-boundary.md), [`docs/retention.md`](./retention.md), [`infra/README.md §17`](../infra/README.md).

---

## 1. Why This Gate Exists

The agent's defense-in-depth model (answer-leakage tool boundary, idempotent grading, per-language quality evaluation, Managed Identity end-to-end, RBAC scoped per resource) is correct **only when every layer is wired**. The pre-public gate is the explicit checkpoint where every layer is verified active **in the target deploy**, not merely "implemented in code."

Pre-public is the moment the threat model changes: the user is no longer a trusted insider; rate limiting must be active; transcripts must hit their compliance retention; the "what does the LLM see" boundary must be reviewed by Security.

---

## 2. Mandatory Checks (release pipeline gate)

### 2.1 Security — boundary

- [ ] `tests/test_no_answer_leakage.py` (TEST-006) green for `en`, `fr`, `es`.
- [ ] `tests/test_prompt_redaction.py` (TEST-018) green for every language × channel.
- [ ] `tests/test_injection_corpus.py` (TEST-023) green for plain + base64 + ROT13 + leet variants in all three languages.
- [ ] AST lint (`tasks/007 TASK-125`): `get_answer_key` is referenced only inside the body of `submit_answer`.
- [ ] [`docs/llm-boundary.md`](./llm-boundary.md) reviewed and signed off by Security within the last 30 days.
- [ ] `grading_event` in App Insights does **not** contain `expected` or `receivedRaw` (TEST-010 + AL-006).

### 2.2 Security — identity & access

- [ ] No connection strings or shared keys in the repo (CI grep, `tasks/007 TASK-120`).
- [ ] All runtime identities are UAMI; no SAMI on data-plane services (`infra/README §3.3` rule 4).
- [ ] RBAC verification script (`tasks/007 TASK-121`) prints `OK`: no `Owner`/`Contributor` on workload UAMIs, no subscription-scoped assignments.
- [ ] `disableLocalAuth=true` on Cosmos, AI Search, App Configuration.
- [ ] `allowSharedKeyAccess=false` on Storage.
- [ ] Key Vault: `enableRbacAuthorization=true`, soft-delete + purge protection enabled.
- [ ] Entra ID end-to-end on text and voice (SECT-005). Anonymous traffic rejected (401).

### 2.3 Security — rate limiting

- [ ] API Management deployed in front of the Hosted Agent endpoint (`tasks/007 TASK-129`).
- [ ] Per-user quotas active: `questions/minute`, `quizzes/day`, `voice-minutes/day` (SEC-011). Values match `infra/main.parameters.<env>.json`.
- [ ] Synthetic quota-breach test (`SECT-007`) returns 429 + `Retry-After`.

### 2.4 Idempotency & state integrity

- [ ] `tests/test_idempotency.py` (TEST-007) green against real Cosmos (not a mock).
- [ ] `tests/test_session_state_machine.py` (TEST-026) green.
- [ ] `tests/test_timers.py` (TEST-027) green, including the sweeper case (`tasks/003 TASK-191`).
- [ ] `tests/test_tool_allowlist.py` (TEST-019) green — dispatcher rejects unknown tools and serializes concurrent `submit_answer`.

### 2.5 Governance

- [ ] `tests/test_prompt_hash.py` (TEST-025) green; prompt-hash verification active in the deployed runtime.
- [ ] `tests/test_refusal_localization.py` (TEST-021) green.
- [ ] `tests/test_coverage_consent.py` (TEST-022) green — no silent language switch under coverage gap.
- [ ] `tests/test_explanation_provenance.py` (TEST-020) green.
- [ ] `tests/test_tts_invariants.py` (TEST-024) green.

### 2.6 Retention & compliance

- [ ] [`docs/retention.md`](./retention.md) reviewed within the last 90 days.
- [ ] Cosmos `sessions` TTL applied to terminal-state rows (30 d). Verified by SECT-008.
- [ ] Cosmos `audit` TTL set to 365 d; `audit-archive` Blob container exists with 7-year immutability policy locked. Verified by SECT-009 + RES-005.
- [ ] Log Analytics retention matches policy (90 d hot in prod). Verified by SECT-008.
- [ ] App Insights `transcript`-bearing events purged at 30 d (PII).
- [ ] GDPR right-to-erasure flow exists, is tested end-to-end, and cascades to `sessions` while pseudonymizing `audit` (audit P2 §5.7).

### 2.7 Per-language quality

- [ ] Foundry Evaluations per language (TEST-011, `tasks/009 TASK-167`) green, parity within tolerance, for every supported language in AppConfig `languages:supported`.
- [ ] Per-language phrasing block has every slot populated (`tasks/004 TASK-062`): `greeting`, `ask_topic`, `frame_question`, `feedback_correct`, `feedback_incorrect`, `topic_unavailable_fallback`, `coverage_gap_consent`, `score_preview_decline`, `refusal_off_topic`, `refusal_answer_key`, `stay_on_task`, `results_summary`, `pass_message`, `fail_message`, `idle_reprompt`.

### 2.8 Observability & alerting

- [ ] App Insights workbook "Quiz Voice — Hot Path" deployed and surfacing live data.
- [ ] App Insights workbook "Quiz Correctness" deployed.
- [ ] Workbook "Security & Governance" deployed with `agent.injection_detected`, `agent.coverage_gap`, `agent.refusal_loop`, `agent.unknown_tool`, `agent.prompt_hash_mismatch`, `agent.output_truncated` event panels.
- [ ] Alerts wired per `infra/README §10.2`: voice tool-call p95 > 300 ms, Cosmos 429 rate > 0.5%, AI Search 503 rate > 0, `agent.prompt_hash_mismatch` any occurrence, answer-leakage canary failure.
- [ ] Action Group routes P0 to pager (PagerDuty in `prod`).

### 2.9 Disaster recovery

- [ ] DR drill executed in the last **90 days** (`infra/README §9.4`). Evidence file present in repo (`docs/dr-drill-<date>.md`).
- [ ] Failed drill remediation: none outstanding.

### 2.10 Cost discipline

- [ ] Budgets deployed per env (`tasks/010 TASK-211`).
- [ ] Budget alert thresholds (50/80/100%) routed to FinOps + on-call.
- [ ] Cost workbook deployed.

---

## 3. CI Enforcement

The release pipeline runs:

```yaml
- name: pre-public-gate
  if: github.ref_type == 'tag' && contains(github.ref_name, 'public-ready')
  run: |
    python tools/pre_public_gate.py --env prod --checklist docs/pre-public-gate.md
```

`tools/pre_public_gate.py` (to be authored as part of `tasks/007 TASK-130`):

1. Parses this document for `- [ ]` items.
2. Looks up each item's evidence (test result, post-deploy assertion, signoff timestamp).
3. Refuses to tag if any item is missing or stale.

---

## 4. Manual Signoff

Two manual signoffs are required and **cannot** be automated:

1. **Security review of [`docs/llm-boundary.md`](./llm-boundary.md)** within the last 30 days. Recorded as a commit on `docs/llm-boundary.md` by a member of the Security group (verified via Entra group membership in the CI job).
2. **DR drill evidence file** in the repo, dated within the last 90 days, signed off by Platform on-call.

---

## 5. Failure Mode

If a check fails:

- The CI job exits non-zero; the `public-ready` tag is rejected.
- The failing check is logged with a remediation pointer.
- The release is blocked until either (a) the failing item is fixed and the gate re-run passes, or (b) Security explicitly approves an exception with a documented compensating control (rare; ADR-grade).

There is no "force release" override in CI.

---

## 6. Out-of-Scope Items

Items deferred to v2+, not gated here:

- Voice biometrics / proctoring (SEC-012).
- Cross-tenant LLM isolation (single-tenant deploy assumed in v1).
- AI-generated questions (curated bank only in v1).
- Adaptive testing flow (sequential MCQ only in v1).

If any of these land in v2, this document gets new sections.
