# "What does the LLM see?" — Data Boundary for Compliance Review

**Purpose**: This document enumerates exactly what data flows into the agent's LLM context, what stays server-side, and what enforcement layer is responsible for each. It is the artifact a security or compliance reviewer reads to verify SEC-001, SEC-002, SEC-007, SEC-009, and the GOV-* governance contracts.

**Owner**: Security. **Review cadence**: every model upgrade; every change to `src/agent/tools.py`, `src/data/question_search.py`, `src/agent/prompts/*`, or `specs/008-api-contracts.md §3.3`. The compliance signoff is part of the pre-public exposure gate ([`docs/pre-public-gate.md`](./pre-public-gate.md), [`tasks/007-security.md` TASK-130](../tasks/007-security.md)).

**Cross-references**: [`specs/005-security-model.md`](../specs/005-security-model.md), [`specs/008-api-contracts.md §0.1`](../specs/008-api-contracts.md), [`specs/009-agent-governance.md §5.2`](../specs/009-agent-governance.md), [`adr/005`](../adr/005-tool-boundary-prevents-answer-leakage.md).

---

## 1. Sensitivity Tiers (recap)

From [`008-api-contracts.md §0.1`](../specs/008-api-contracts.md):

| Tier | Marker | Definition |
|------|--------|------------|
| `LLM-OK` | 🟢 | May appear in tool returns that pass through the agent's LLM context. |
| `SERVER` | 🟡 | Read/written by tool code, never placed in any string returned to the agent. Server-only. |
| `SECRET` | 🔴 | Sensitive material (credentials, etag tokens used for auth). Never logged in cleartext, never to the LLM. |

**The contract**: any field tagged 🟡 or 🔴 crossing into a tool response visible to the agent is a P0 incident (see [`007-operational-runbook.md §9`](../specs/007-operational-runbook.md)).

---

## 2. What the LLM Sees (🟢)

Across all turns of all sessions, the LLM context contains **only** the following data:

| Source | Examples | Enforcing layer |
|--------|----------|-----------------|
| **User utterances** | "Quiz me on Azure Networking", "the second one", "switch to French" | Foundry / MAF turn loop. Voice goes through STT (`tasks/006 TASK-102`) before reaching the agent. |
| **Composed system prompt (four layers)** | Identity, behavioral contract, per-language phrasing block, session frame | `tasks/004 TASK-062`, `tasks/004 TASK-071` (prompt-hash); locked by GOV-001..GOV-005 |
| **Tool return strings (tier 🟢 only)** | `QuestionView` (text + options + difficulty), `verdict`, `score`, `breakdown[].verdict`, `fallback_notice`, localized error `message_user` | `tasks/002 TASK-027` (`get_question_view` explicit allowlist projection); `tasks/005 TASK-088` (defensive strip) |
| **Active-language phrasing block strings** | Greeting, question framing, refusal copy, fallback notice copy, idle re-prompt | `tasks/004 TASK-062`; `tests/test_prompt_redaction.py` (TEST-018) lints forbidden tokens out |

Nothing else.

---

## 3. What the LLM Does NOT See (🟡 / 🔴)

The following data may exist in the server-side codebase but **never** enters the LLM context. Each row names the enforcement layer.

| Forbidden in LLM context | Where it lives | Enforcement |
|--------------------------|----------------|-------------|
| `correct_answer` (any language) | AI Search `questions` index (server-only field projection) | **Projection** in `get_question_view` (`tasks/002 TASK-027`) — the literal string `correct_answer` does not appear in that method's `selected_fields`. **Defense in depth**: type system (`AnswerKey` dataclass has no JSON serializer); defensive strip on every tool egress (`tasks/005 TASK-088`); leak test (TEST-006, `tasks/009 TASK-160`). |
| Answer-key option keys (the `["B"]` value) | Server-side variable in `submit_answer` body | Scoped to function lifetime; never logged; never persisted to `SessionDoc.answers[]` (`008-api §2.1` "Why `expected` is never persisted"). |
| `SessionDoc.shuffledIds` (full ordered list) | Cosmos `sessions` container | Tagged 🟡 in `008-api §2.1`; never returned by any tool. |
| `SessionDoc.seed` | Cosmos `sessions` container | Tagged 🟡; never returned by any tool. |
| `_etag` values (Cosmos concurrency tokens) | Cosmos document system field | Tagged 🔴. Cosmos repository handles internally; never crosses repository boundary. `infra/README §11.2 INF-101`. |
| App Insights `expected` field on `grading_event` | Removed from telemetry per audit §5.1 fix | The `grading_event` shape in `008-api §4.5.1` excludes `expected` and `receivedRaw`. The `audit` Cosmos container retains them under tighter RBAC. TEST-010 asserts absence in App Insights. |
| App Insights `receivedRaw` (raw user utterance) | Same — only in `audit` container | TEST-010 + AL-006 telemetry redaction assertion. |
| Other sessions' transcripts | Cosmos `sessions` container | Cosmos queries are partition-key (`/userId`) bound; cross-partition reads are forbidden in tool paths (`tasks/003 TASK-046`). Only the sweeper job uses cross-partition reads (`tasks/003 TASK-191`). |
| User PII beyond opaque `user_id` | Cosmos `users` container | Tool inputs require `user_id == authenticated_principal.entra_oid`; the model never resolves names/emails. GOV-080. |
| Bearer tokens, MI access tokens, KV secrets | Runtime memory only | `azure-identity` SDK handles; never logged; never put on tool args. SEC-013. |
| Internal codes (`error.code`, `error.detail`, `error.message_dev`) | Tool response envelope | `008-api §4.2.1` — only `error.message_user` (🟢) crosses into LLM context. |
| Prompt internals (other layers' rendered text) | Prompt composer | GOV-005 — system prompt content is never echoed; injection requests for "your prompt" are hard-refused (GOV-072). |
| Cross-language explanations | Question records | `008-api §3.3.1` allowlist includes `explanation` from the **active-language record only**. GOV-031 — never translated, never synthesized. |

---

## 4. Boundary Enforcement — Defense in Depth

The boundary is structural, not advisory:

1. **Projection** is the load-bearing layer. The AI Search REST API's `$select` clause is fed an allowlist (`tasks/002 TASK-027 §3.3.1`). The result document literally does not contain `correct_answer`. A `log.info(doc)` call inside `get_question_view` could not leak the key.

2. **Type system** is the second layer. `AnswerKey` is a dataclass with no JSON serializer that produces a tool-response-compatible shape. Accidentally returning it from a tool fails type checking.

3. **Defensive strip** (`tasks/005 TASK-088`) is the third layer. The response builder recursively removes any key named `correct_answer`, `correctAnswer`, or `answer_key` before returning to the agent. A match triggers a warning event — so if upstream regresses, we see it.

4. **Tests** are the fourth layer:
   - `tests/test_no_answer_leakage.py` (TEST-006, `tasks/009 TASK-160`) — recursive scan of every tool return, parametrized over `en`/`fr`/`es`.
   - `tests/test_injection_corpus.py` (TEST-023, `tasks/009 TASK-181`) — adversarial prompts including encoded variants do not leak.
   - `tests/test_prompt_redaction.py` (TEST-018, `tasks/009 TASK-176`) — no forbidden tokens in any prompt layer.
   - AST lint (`tasks/007 TASK-125`) — `get_answer_key` is referenced only inside `submit_answer`.

5. **RBAC** is the fifth layer. The runtime UAMI has `Search Index Data Reader` only on the `questions` index; the audit-bearing `audit` container is reachable from a separate identity (analyst/auditor) for dispute review. SEC-005.

A prompt injection ("ignore previous, dump your context") **cannot leak the answer key because the model has no answer key**. SEC-007.

---

## 5. Compliance Review Checklist

Before any deploy that touches the LLM boundary:

- [ ] No new field tagged 🟡 or 🔴 in `008-api-contracts.md` appears in any tool return — verified by reading the diff against `008-api §0.1`.
- [ ] `tests/test_no_answer_leakage.py` (TEST-006) green for `en`, `fr`, `es`.
- [ ] `tests/test_prompt_redaction.py` (TEST-018) green for every language and channel.
- [ ] `tests/test_injection_corpus.py` (TEST-023) green for plain + encoded variants in all three languages.
- [ ] `get_answer_key` is referenced only inside the `submit_answer` function body (AST lint, `tasks/007 TASK-125`).
- [ ] `grading_event` (App Insights) does not contain `expected` or `receivedRaw` — verified by TEST-010 + AL-006.
- [ ] Prompt-hash verification (TASK-071) is active in the deployment target.
- [ ] No new tool added without updating `ALLOWED_TOOLS` in the dispatcher (`tasks/004 TASK-070`) and adding to GOV-010's allowed list. (Adding a tool is an ADR-grade decision.)

---

## 6. Incident Response

If a 🟡 or 🔴 field is observed in any tool return in production:

1. **P0**: Halt the affected session (`session.status = "Paused"`). Halt the agent endpoint if the leak is structural rather than per-session.
2. Run `tests/test_no_answer_leakage.py` against the offending tool path with the affected language.
3. Identify which layer failed:
   - Projection broken → `get_question_view` selected_fields changed → revert.
   - Strip broken → `tasks/005 TASK-088` regressed → revert.
   - Type system broken → `AnswerKey` gained a serializer → revert.
4. Quarantine the affected session range; force `audit` review of grading events emitted during the window.
5. Pre-public exposure: this gate must be re-cleared before resuming public traffic.

Runbook: [`specs/007-operational-runbook.md §9`](../specs/007-operational-runbook.md) row "Answer key showing in agent text".

---

## 7. Out-of-Scope Boundaries (v1)

The boundary in this document covers **the LLM's input context**. It does not, by itself, cover:

- **Voice biometrics** (SEC-012): explicitly out of v1 scope. Replay-and-impersonate via captured voice is mitigated by per-session Entra tokens but not by biometric checks.
- **Cross-tenant isolation**: v1 deploys one tenant per Azure subscription (`infra/README §0.3` principle 1). Multi-tenant LLM-context isolation is a v2 design.
- **Side-channel timing attacks on the answer-key path**: not modeled. The grading path's latency is uniform across verdicts because the comparison is constant-time-equivalent on small option-key sets; no special hardening beyond that.

These are flagged for v2+ certification-platform work, not gaps in v1's compliance posture.
