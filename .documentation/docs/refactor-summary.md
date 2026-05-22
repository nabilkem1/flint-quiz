# Refactor Summary — `docs/initial-plan.md` → Spec Kit Structure

This document explains how the monolithic `docs/initial-plan.md` was split into a Spec Kit structure (`specs/` + `adr/`), what improvements were made, what ambiguities surfaced during the refactor, and the recommended next steps.

## 1. Files Produced

### Specs

| File                                          | Source sections from `initial-plan.md`                                                                                                |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `specs/001-product-requirements.md`           | §Context, §v1 deliverables, §C (multilingual scope), §D (voice scope), §I (risks), §J (future enhancements)                          |
| `specs/002-system-architecture.md`            | §A (verdict, comparison, mermaid diagrams), §E (state architecture), §G (orchestration), §D (voice path diagram), parts of §H (scale) |
| `specs/003-data-contracts.md`                 | §C (question record shape), §F (data storage map + AI Search config), tool signatures from §B                                        |
| `specs/004-agent-behavior.md`                 | §B (single-agent rationale, tools, security boundary, voice considerations), §C (language resolution, agent instructions)            |
| `specs/005-security-model.md`                 | §H (auth, RBAC, idempotency, security-exam-specific), §I (top risks)                                                                  |
| `specs/006-testing-strategy.md`               | §L (verification plan), test files listed in §K                                                                                       |
| `specs/007-operational-runbook.md`            | §H (observability, reliability, scalability, cost), §M (phasing), §K (files to create), §Sources                                     |

### ADRs

| File                                                          | Captures                                                                                                                              |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `adr/001-use-microsoft-agent-framework.md`                    | The choice of MAF (Python) on Foundry Agent Service over Prompt Flow, LangGraph-alone, Foundry Workflows, and Durable Functions.       |
| `adr/002-single-agent-architecture.md`                        | The choice of one agent (not multi-agent) for v1, and the trigger conditions for revisiting.                                          |
| `adr/003-use-cosmos-db-for-session-state.md`                  | The choice of Cosmos as the durable system of record (not LLM context, not thread-only, not Redis, not SQL).                          |
| `adr/004-use-ai-search-for-question-bank.md`                  | The choice of AI Search for the multilingual bank, with Blob as the authoring source.                                                 |
| `adr/005-tool-boundary-prevents-answer-leakage.md`            | The non-negotiable tool boundary: `correct_answer` never enters LLM context; only `submit_answer` reads it server-side.               |

## 2. Content Movement (Section-by-Section)

| Original section in `initial-plan.md`           | New home(s)                                                                                                          |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Context / v1 deliverables                       | `specs/001-product-requirements.md` §1–2                                                                             |
| §A — Recommended architecture (verdict)         | `specs/002-system-architecture.md` §1; mirrored in `adr/001`, `adr/002`                                              |
| §A — Stack comparison table                     | `specs/002-system-architecture.md` §2; rationale in `adr/001`                                                        |
| §A — Mermaid (architecture, sequence, state)    | `specs/002-system-architecture.md` §3–5 (preserved verbatim)                                                         |
| §B — Agent design (single-agent, tools)         | `specs/004-agent-behavior.md` §1–4; rationale in `adr/002`                                                           |
| §B — Tool contract / security boundary          | `specs/004-agent-behavior.md` §4, `specs/005-security-model.md` §4, `adr/005`                                        |
| §B — Voice considerations in tool design        | `specs/004-agent-behavior.md` §5                                                                                     |
| §C — Multilingual design                        | `specs/001-product-requirements.md` §3 (FRs), `specs/003-data-contracts.md` §2 (schema), `specs/004-agent-behavior.md` §7 (behavior) |
| §D — Voice design                               | `specs/001-product-requirements.md` §3 (FRs), `specs/002-system-architecture.md` §9 (voice path + diagram), `specs/004-agent-behavior.md` §5/§8 |
| §E — State management                           | `specs/002-system-architecture.md` §7, `adr/003`                                                                     |
| §F — Data storage                               | `specs/003-data-contracts.md` §1–5, `adr/003`, `adr/004`                                                              |
| §G — Orchestration                              | `specs/002-system-architecture.md` §8, `adr/001`                                                                     |
| §H — Production considerations                  | Split across `specs/005-security-model.md` (auth/security parts) and `specs/007-operational-runbook.md` (observability, reliability, scalability, cost, rate limiting) |
| §I — Top 3 risks                                | `specs/001-product-requirements.md` §7 + cross-referenced in `specs/005-security-model.md` §10                       |
| §J — Future enhancements                        | `specs/001-product-requirements.md` §6                                                                                |
| §K — Critical files                             | `specs/007-operational-runbook.md` §1                                                                                 |
| §L — Verification plan                          | `specs/006-testing-strategy.md` §1                                                                                    |
| §M — Implementation phasing                     | `specs/007-operational-runbook.md` §7                                                                                 |
| §Sources                                        | `specs/007-operational-runbook.md` §10                                                                                |

## 3. Traceability IDs Introduced

To make cross-document references unambiguous and to support future requirements-traceability matrices, every requirement-bearing statement now carries an ID:

- **FR-001 … FR-015** — Functional requirements (`specs/001-product-requirements.md` §3).
- **NFR-001 … NFR-014** — Non-functional requirements (`specs/001-product-requirements.md` §4).
- **SEC-001 … SEC-014** — Security requirements (`specs/005-security-model.md` §2).
- **TEST-001 … TEST-011** — Verification / testing requirements (`specs/006-testing-strategy.md` §1).

Cross-document references use these IDs consistently (e.g., "see SEC-001/SEC-002" or "NFR-002, SEC-006").

## 4. Major Improvements

1. **Separation of concerns**: Product requirements, system architecture, data contracts, agent behavior, security, testing, and operations now live in dedicated files. Reading order is predictable.
2. **Traceability**: FR/NFR/SEC/TEST IDs let any code change, PR, or audit reference a specific requirement. Tests and code can carry "implements TEST-006 / SEC-001" comments.
3. **ADRs capture the *why***: The five most consequential decisions are written up with Context / Decision / Alternatives / Consequences / Revisit-When, making it possible for a future reader to know whether a decision is still valid without re-reading the entire architecture document.
4. **Diagrams preserved verbatim**: All three Mermaid diagrams (architecture flowchart, quiz lifecycle sequence, session state) are kept; the voice-path sequence diagram is also preserved in `specs/002-system-architecture.md` §9.
5. **Security boundary made explicit**: The "tools never return `correct_answer`" rule is restated in `specs/004-agent-behavior.md`, `specs/005-security-model.md`, `adr/005`, and tied to TEST-006 — so the property is hard to lose in a refactor.
6. **Operational runbook is actionable**: Checklists (pre-deploy, post-deploy smoke, per-release quality gate, pre-public-exposure gate) and an incident triage table (`§9`) make the runbook usable, not just descriptive.
7. **Cross-linking**: Every spec links to relevant ADRs and back; ADRs link to the specs that operationalize them.
8. **Standardized terminology**: "Question bank", "session", "answer key", "tool boundary", "TTS-friendly", "language code (ISO 639-1)" are consistent across all files.

## 5. Ambiguities Discovered

These are points where the original plan was implicit or under-specified. They are flagged here for follow-up decisions; none of them block v1 implementation, but each is worth resolving deliberately.

1. **Pass/fail threshold**: §A and §L mention "pass/fail" in the final result but no threshold is defined. Suggestion: parameterize per topic in the `topics` container or in App Configuration.
2. **Per-quiz `timeLimitSeconds`**: NFR-004 says timers are server-side, but the default value and per-topic overrides are not defined.
3. **Difficulty mix**: §A's sequence diagram references "difficulty mix" filtering at `start_quiz`, but the exact distribution (e.g., 30% easy / 50% medium / 20% hard) is not specified.
4. **Score weighting + partial credit**: `score_weight` and partial-credit behavior are implied by `tests/test_grading.py` but no canonical formula is given. Suggestion: define in `specs/003-data-contracts.md` once decided.
5. **Resume window**: How long after disconnection can a user resume? TTL on `sessions` defines an upper bound, but the user-facing resume window is not specified.
6. **Channel-switch semantics for active question**: If the user has heard Q3 in voice but has not answered, do they get the same Q3 in text on resume? Implementation note: yes, because `currentIndex` is in Cosmos — but make this explicit.
7. **Topic fallback policy** (FR-012): "closest available language" needs a defined ordering (e.g., French → Spanish → English, or fall back to English always?). Currently informal.
8. **Multilingual voice mapping**: §C mentions "`nova` for `en`, `alloy` adapted for `fr`/`es`" but the canonical voice-per-language map should live in App Configuration.
9. **Audit retention vs. session retention**: Both are mentioned but exact values are TBD.
10. **API Management quotas** (SEC-011): "per-user quotas" are scoped but specific limits (questions/minute, quizzes/day, voice-minutes/day) are not set.
11. **Explanation visibility**: ADR 005 notes `explanation` is the "controlled disclosure path", but the policy for *when* the explanation is shown (always after answer? only on incorrect? user-toggleable?) is not specified.

## 6. Internal Consistency Checks Performed

- All references between specs and ADRs verified (e.g., `specs/004` references `adr/005`; `adr/003` references `specs/003`).
- Risk #1 (answer leakage) is consistently tied to SEC-001/SEC-002/SEC-007 and TEST-006 in all four files that mention it (`specs/001`, `specs/004`, `specs/005`, `adr/005`).
- Risk #2 (idempotency) is consistently tied to NFR-002/SEC-006 and TEST-007.
- Risk #3 (per-language drift) is consistently tied to NFR-010 and TEST-011.
- The "one record per `(logical_id, language)` pair" rule is in `specs/003-data-contracts.md` (NFR-011) and `adr/004`, and referenced by `specs/004-agent-behavior.md` §7.
- Voice latency budget (NFR-001) is referenced in `specs/002` §9, `specs/004` §11, and `specs/007` §2.3.
- Single-agent rationale appears in `specs/004` and is justified once in `adr/002` (avoiding duplicated rationale).

## 7. Recommended Next Steps

1. **Resolve the ambiguities in §5** by adding short addenda or by deciding inline in the relevant spec. Most are 1–2 sentence decisions.
2. **Generate skeleton code** matching `specs/007-operational-runbook.md` §1 (Critical Files). The split between `src/data/question_search.py`'s two methods (public vs. server-only) is the highest-leverage early piece — it locks the security boundary into the code shape.
3. **Add per-language Foundry Evaluation harness** (NFR-010, TEST-011) early; it's the protection against the silent drift risk.
4. **Write `tests/test_no_answer_leakage.py` first**. It encodes the most important property (SEC-001/SEC-002) and will guard every subsequent change to the tool layer.
5. **Decide retention policies** (sessions TTL, transcript retention, audit retention) — these gate compliance review (SEC-008, SEC-009).
6. **Stand up a thin App Configuration baseline**: language allowlist, voice-per-language map, model deployment name, search endpoint, supported languages, pass-threshold defaults. This is cheap and removes several hard-coded constants.
7. **Add a v2 roadmap doc** once Phase 4 starts becoming concrete — particularly the adaptive-testing introduction (where ADR 002 says to reconsider multi-agent).

## 8. What Was Preserved Verbatim

- All three Mermaid diagrams (architecture flowchart, quiz lifecycle sequence, session state machine), plus the voice-path sequence diagram.
- The question record JSON example.
- The full alternative-stack comparison table.
- The verification plan (now TEST-001 … TEST-011).
- The implementation phasing (Phase 1 → Phase 4).
- The data-storage map.
- The Sources / references list.

## 9. What Was Removed or De-Duplicated

- The "system uptime is not the metric that matters here — grading correctness is" line appears once now (in `specs/007-operational-runbook.md` §2.2), instead of being scattered.
- Repeated security-boundary statements were consolidated into a single canonical statement in ADR 005, with cross-references from the specs that need to invoke it.
- The "Microsoft consolidated its agent story in 2026" context appears once (in `specs/001-product-requirements.md` §1) with cross-reference from ADR 001's Context section.

No critical information was dropped. All rationale, tradeoffs, security considerations, operational concerns, and testing strategy from the original document are present in the refactored structure.
