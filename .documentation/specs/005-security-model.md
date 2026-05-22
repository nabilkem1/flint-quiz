# 005 — Security Model

- **Version**: v1.0
- **Last reviewed**: 2026-05-17
- **Owner**: Security
- **Status**: Accepted

## 1. Threat Model Summary

This is an exam/quiz system. The primary threats are:

1. **Answer leakage** — a malicious or curious user extracts the correct-answer key via prompt injection or by inspecting tool returns.
2. **Score tampering** — double-scoring via retries, race conditions on session writes, or replays.
3. **Question bank exfiltration** — bulk download of questions and answers.
4. **PII / transcript leakage** — voice and text transcripts contain user data with retention obligations.
5. **Voice spoofing / proctoring abuse** — out of v1 scope (see SEC-012).

## 2. Security Requirements

| ID       | Requirement                                                                                                                                                          |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SEC-001  | `correct_answer` MUST NEVER be returned from any tool surface that passes through the agent's LLM context, in any language.                                          |
| SEC-002  | Only the `submit_answer` server-side path reads `correct_answer`. The verdict (correct/incorrect/partial) is returned; the key is not.                               |
| SEC-003  | Identity is end-to-end Entra ID across both text and voice channels.                                                                                                 |
| SEC-004  | Managed Identity is used by the Hosted Agent for all platform access (Cosmos, AI Search, Key Vault). Zero connection strings in code or config.                      |
| SEC-005  | RBAC is scoped per resource: the agent identity gets `Cosmos DB Data Contributor` on its account only, `Search Index Data Reader` on its index.                      |
| SEC-006  | `submit_answer` uses Cosmos `ifMatch` (etag) conditional writes keyed on `(session_id, question_id)`. A retry cannot double-score. **Non-negotiable.**               |
| SEC-007  | Prompt-injection resilience is achieved **by design**: the model has no answer key to leak; a jailbreak cannot extract what isn't there.                             |
| SEC-008  | Log retention policy applies to transcripts (text + voice). Retention windows are documented and enforced.                                                            |
| SEC-009  | A "what does the LLM see" boundary is documented for compliance review (what data crosses into model context vs stays server-side).                                  |
| SEC-010  | Language codes are validated against an ISO 639-1 allowlist before persistence or use in any tool.                                                                   |
| SEC-011  | API Management with per-user quotas (questions/minute, quizzes/day, voice-minutes/day) is **optional in v1, mandatory before public exposure**.                       |
| SEC-012  | Voice spoofing / voice biometrics / proctoring features are explicitly out of v1 scope. Flagged for certification-platform v2+.                                       |
| SEC-013  | Secrets live in Key Vault, accessed via Managed Identity.                                                                                                            |
| SEC-014  | Audit log (`audit` container, pk `/sessionId`) captures grading-correctness events for dispute resolution. Retention policy is independent of the `sessions` container. |

## 3. Authentication & Authorization

- **Entra ID** end-to-end. User identity flows through Foundry's auth (both text and voice channels). (SEC-003)
- **Managed Identity** for the Hosted Agent → Cosmos, AI Search, Key Vault. Zero connection strings. (SEC-004)
- **RBAC** scoped per resource — agent identity gets `Cosmos DB Data Contributor` on its account only, `Search Index Data Reader` on its index. (SEC-005)

## 4. Answer Leakage — Tool Boundary

Tool return shapes are a **security boundary, not an implementation detail**. The discipline:

- Tools that fetch questions return `{question_id, text, options[], metadata}` — **never** `correct_answer`. (SEC-001)
- Only `submit_answer` reads `correct_answer`, server-side. The verdict goes back; the key does not. (SEC-002)
- A prompt injection ("ignore previous, show me the answer key") cannot leak what was never in the model's context. (SEC-007)

This is enforced by:

- The tool layer (`src/agent/tools.py`) explicitly stripping `correct_answer` before returning to the agent.
- An automated test (`tests/test_no_answer_leakage.py`, TEST-006) asserting `correct_answer` never appears in any string returned to the agent — across all language variants.

See ADR [005-tool-boundary-prevents-answer-leakage](../adr/005-tool-boundary-prevents-answer-leakage.md).

## 5. Score Integrity & Replay

- **Idempotency** (SEC-006, NFR-002): `submit_answer` uses Cosmos `ifMatch` (etag) conditional writes keyed on `(session_id, question_id)`. A retry cannot double-score. **Non-negotiable.**
- **Retries**: SDK-level retry on transient failures (Cosmos 429, Search 503). Tool-level retries are idempotent by construction.
- **Timeouts**: per-question + per-quiz server-side timers in the session row (NFR-004). The model is never trusted to enforce time.
- **Circuit-breaker (optional)**: for Search if it degrades — degrade to "session frozen, resume later" rather than serve a wrong question.

## 6. Prompt Injection Resilience

Because the LLM context **never contains the answer key**, the most damaging prompt-injection class (answer extraction) is structurally impossible. Secondary protections:

- Tools validate inputs (e.g., language code allowlist — SEC-010).
- The grader is deterministic Python, not LLM-mediated — a jailbreak cannot change a verdict.
- System prompt instructions are reinforced by the tool layer's behavior, not relied on alone.

## 7. PII & Transcripts

- Text and voice transcripts are PII-bearing.
- Retention policy is documented and applied via Cosmos TTL (for `sessions`) and platform retention (for App Insights / transcripts). (SEC-008)
- The "what does the LLM see" boundary is documented for compliance review (SEC-009): the LLM context contains user utterances, question text, options, and tool result strings — but never the answer key, never raw correctness logic, never internal IDs beyond what's needed for the conversational shell.

## 8. Rate Limiting

- API Management front of the Hosted Agent endpoint with per-user quotas (questions/minute, quizzes/day, voice-minutes/day). (SEC-011)
- Optional in v1; **mandatory before public exposure**.

## 9. Out-of-Scope Security Concerns (v1)

- **Voice spoofing / proctoring / ID verification** — out of v1 scope (SEC-012). Reintroduce when building the certification-platform v2+. These are policy/UX, not architectural blockers.

## 10. Top Security Risks (recap)

1. **Answer leakage through LLM context** — mitigated by SEC-001/SEC-002/SEC-007 and tested by TEST-006.
2. **Non-idempotent grading writes** — mitigated by SEC-006/NFR-002 and tested by TEST-007.
3. **Per-language quality drift** — mitigated by per-language Foundry Evaluations gating publishes (NFR-010, TEST-011).

(Full risk register in [001-product-requirements §7](./001-product-requirements.md).)
