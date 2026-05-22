# Phase Progression — Flint Quiz

**Purpose**: The Phase 1 → Phase 2 → Phase 3 delivery plan, with the checkpoint at each phase boundary and the **feature freeze** rule that prevents scope creep.

**Owner**: Platform + Release. **Cross-references**: [`specs/007-operational-runbook.md §7`](../specs/007-operational-runbook.md), [`tasks/010-deployment.md` TASK-212](../tasks/010-deployment.md), [`docs/ai-agent-development-guidelines.md`](./ai-agent-development-guidelines.md), [`docs/pre-public-gate.md`](./pre-public-gate.md).

---

## 1. Why Phases (Not a Big Bang)

Each phase ends with a documented checkpoint AND a passing smoke. Phases exist to:

1. **Prevent scope creep** — feature freeze per phase. New work goes into the next phase's plan, not into the current phase. Adding a "small" enhancement mid-phase is the single most reliable way to miss the checkpoint.
2. **Make progress observable** — the post-phase smoke is the load-bearing proof. A phase that doesn't smoke is not done, regardless of how complete the code looks.
3. **Bound rollback** — if a phase regresses, rollback is to the previous phase's checkpoint commit, not to an arbitrary point.

---

## 2. Phase 1 — PoC Core (2–3 days)

**Goal**: text-channel quiz works end-to-end in one language on one topic. The agent answers questions; Cosmos persists state; AI Search returns questions.

### 2.1 Deliverables

| ID  | Item                                                                      | Pack                      |
|-----|---------------------------------------------------------------------------|---------------------------|
| 1   | Bicep skeleton — RG, Cosmos, AI Search, Foundry account, UAMIs            | 001-infrastructure        |
| 2   | One topic with ≥ 10 questions × 3 languages authored                      | seed                      |
| 3   | MAF agent + 5 tools (`list_topics`, `set_language`, `start_quiz`, `submit_answer`, `get_results`) | 004 + 005 |
| 4   | Cosmos `sessions` + `users` + `topics` containers                         | 003                       |
| 5   | AI Search `questions` index with the two-method projection                | 002                       |
| 6   | Playground text mode reaches the agent                                    | 010 / Playground          |

### 2.2 Checkpoint

* `azd up` succeeds end-to-end.
* Post-provision hook prints `OK` for every resource.
* TEST-003 (text English smoke) green.
* Cosmos shows one `Scored` session row after the smoke.
* No `correct_answer` in any tool return payload (TEST-006 in en).

### 2.3 Feature Freeze

Phase 1 ends when 2.2 passes. **Do not** start any of the following inside Phase 1:

- Voice channel (Phase 2).
- Per-language Foundry Evaluations (Phase 3).
- APIM rate limiting (Phase 3).
- The GDPR cascade (Phase 3).

---

## 3. Phase 2 — Voice + Hardening (3–4 days)

**Goal**: voice channel works end-to-end; the answer-leakage boundary is tested; idempotency is provable.

### 3.1 Deliverables

| ID  | Item                                                            | Pack                  |
|-----|-----------------------------------------------------------------|-----------------------|
| 1   | Foundry Realtime endpoint wired                                 | 006-voice-realtime    |
| 2   | Answer normaliser (en/fr/es) + TTS-friendly returns             | 005 TASK-086/087      |
| 3   | Conditional-write idempotency on Cosmos sessions                | 003 TASK-047 + 007 TASK-131 |
| 4   | Leak tests (TEST-006) + AST guard on `get_answer_key`           | 009 TASK-160 / 007 TASK-125 |
| 5   | Grading observability — `grading_event` emission                | 008 TASK-141          |
| 6   | Managed Identity end-to-end audit                               | 007 TASK-120/121      |
| 7   | App Insights + Foundry tracing wired                            | 008 TASK-140          |
| 8   | Voice hot-path workbook + alert                                 | 006 TASK-109 / 008 TASK-142 |

### 3.2 Checkpoint

* `azd up` clean from a fresh subscription.
* TEST-003 (en text), TEST-004 (fr text), TEST-005 (es voice) all green.
* TEST-006 green across en/fr/es.
* TEST-007 (real-Cosmos idempotency) green.
* TEST-010 (grading_event present + `expected`/`receivedRaw` absent in App Insights) green.
* Voice tool-call p95 ≤ 300 ms in the workbook for the smoke run.

### 3.3 Feature Freeze

Phase 2 ends when 3.2 passes. **Do not** start any of the following inside Phase 2:

- Per-language Foundry Evaluations (Phase 3).
- APIM rate limiting (Phase 3).
- The GDPR cascade (Phase 3).
- Cost dashboards (Phase 3).

---

## 4. Phase 3 — Operational Polish (2–3 days)

**Goal**: ready to expose publicly. Per-language quality is gated; retention is applied; APIM is wired; rollback is rehearsed.

### 4.1 Deliverables

| ID  | Item                                                            | Pack                  |
|-----|-----------------------------------------------------------------|-----------------------|
| 1   | Per-language Foundry Evaluation gate                            | 009 TASK-167          |
| 2   | Cosmos TTL on terminal-state sessions + audit retention         | 003 TASK-050/051 + 007 TASK-132/133 |
| 3   | APIM rate limiting (off by default in dev; on in prod)          | 007 TASK-129          |
| 4   | Runbook saved queries wired to dashboards                       | 008 TASK-148          |
| 5   | Cost dashboard + 50/80/100% budget alerts                       | 008 TASK-147 + 010 TASK-211 |
| 6   | Channel-switch test (voice → text on same `session_id`)          | 009 TASK-171          |
| 7   | GDPR right-to-erasure cascade + Entra-group gate                | 007 TASK-134          |
| 8   | Pre-public exposure gate (CI + manual signoff)                  | 007 TASK-130 + 010 TASK-209 |

### 4.2 Checkpoint

* All Phase 2 tests still green.
* TEST-011 (per-language Foundry Evaluation) green for every supported language.
* TEST-022 (coverage-fallback consent flow) green.
* TEST-028 (GDPR erasure cascade) green: cascade + repeat + auth-negative + salt rotation.
* `make pre-public-check` passes (every box in `docs/pre-public-gate.md` is `- [x]`).
* `make rollback TAG=<previous>` dry-run completes; smoke matrix re-runs green.

### 4.3 Public-Ready

Phase 3 ends when 4.2 passes AND the pre-public gate (`tools/pre_public_gate.py`) exits 0. At that point the release pipeline accepts a `public-ready` tag. Public-traffic exposure follows.

---

## 5. Scope-Freeze Enforcement

The freeze rule is enforced procedurally, not by tooling:

1. **Plan additions go into the NEXT phase's list**, never the current one.
2. **A mid-phase ADR is the escape hatch.** If a change is genuinely load-bearing for the current phase's checkpoint, raise an ADR; do not slip it in silently.
3. **The smoke matrix is the truth.** A phase that smokes red on the checkpoint date is not done.
4. **Re-baselining the per-language eval is yearly OR on model change** (FORBIDDEN ACTIONS in TASK-009). Silent re-baselining defeats Phase 3's gate.

---

## 6. Phase History Log

| Phase | Started | Ended | Checkpoint commit | Smoke result |
|-------|---------|-------|--------------------|---------------|
| 1     | TBD     | TBD   | TBD                | TBD           |
| 2     | TBD     | TBD   | TBD                | TBD           |
| 3     | TBD     | TBD   | TBD                | TBD           |

Update this table at every checkpoint. The commit + smoke pair is the audit trail of when each phase actually crossed the line.

---

## 7. Cross-Reference

* Operational runbook: [`specs/007-operational-runbook.md §7`](../specs/007-operational-runbook.md).
* Pre-public gate: [`docs/pre-public-gate.md`](./pre-public-gate.md).
* Rollback procedure: [`docs/rollback.md`](./rollback.md).
* Test verification IDs: [`tests/README.md`](../tests/README.md).
