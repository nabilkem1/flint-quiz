# 001 — Product Requirements

- **Version**: v1.0
- **Last reviewed**: 2026-05-17
- **Owner**: Product
- **Status**: Accepted

## 1. Context

You have an empty Azure AI Foundry project and want to build a conversational quiz/exam system where a user chats with an agent, picks a topic, gets N questions drawn from a curated bank of Y, answers them interactively, and gets a scored result with persistent history.

**Scope confirmed**: Python · production-grade reference architecture · curated static question bank · Foundry Playground as the v1 frontend · **multilingual in v1** · **voice in v1**.

**Why this matters now (May 2026 landscape)**: Microsoft's agent story consolidated this year. Prompt Flow is being retired (feature dev ended 2026-04-20; full retirement 2027-04-20). Microsoft Agent Framework (MAF) hit GA on 2026-04-03 as the strategic successor. Foundry Agent Service is GA with managed Cosmos-backed threads and a Realtime (voice) API. Building on Prompt Flow today would be technical debt on day one — this plan anchors on the GA stack.

## 2. v1 Deliverables

- Working agent in Foundry Playground (text + voice).
- Multilingual question bank and conversation. Initial languages: English (`en`), French (`fr`), Spanish (`es`) — extensible via author + reindex (no code change).
- Production-grade hardening (idempotency, observability, RBAC, secrets in Key Vault).
- Architecture plan stored in `./initial-plan.md` in the project repo.

## 3. Functional Requirements

| ID      | Requirement                                                                                                                                                                                |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| FR-001  | User can chat with the agent in natural language and request a quiz on a chosen topic.                                                                                                     |
| FR-002  | User selects a topic from a curated catalog. Topics are localized per supported language.                                                                                                  |
| FR-003  | User requests N questions; the agent draws N from the question bank for that topic and presents them sequentially.                                                                         |
| FR-004  | User receives interactive scored quiz with persistent history (sessions, answers, results).                                                                                                |
| FR-005  | System supports multilingual conversation in v1. Initial languages: English, French, Spanish. Schema/infra accept arbitrary ISO 639-1 codes.                                               |
| FR-006  | System supports a **text channel** via Foundry Playground.                                                                                                                                 |
| FR-007  | System supports a **voice channel** via Foundry Realtime API on the same agent instance.                                                                                                   |
| FR-008  | User may resume a quiz mid-flow by `session_id` after disconnection.                                                                                                                       |
| FR-009  | User may switch channels mid-quiz (start in voice, finish in text — or vice versa) on the same `session_id`.                                                                               |
| FR-010  | User's language preference is persisted on the `users` record once detected or explicitly set.                                                                                             |
| FR-011  | If user has not set a language, the agent detects the language from the user's first message.                                                                                              |
| FR-012  | If a topic lacks coverage in the requested language, the agent falls back to the closest available language and explicitly notifies the user.                                              |
| FR-013  | At quiz completion, the user receives a final score, percentage, pass/fail, and per-question breakdown — in the user's session language.                                                   |
| FR-014  | User may set their preferred language explicitly at any time ("respond in French").                                                                                                        |
| FR-015  | Quiz timing is enforced server-side via per-question and per-quiz timers; expired quizzes auto-grade remaining questions as unanswered.                                                    |

## 4. Non-Functional Requirements

| ID       | Requirement                                                                                                                                                                  |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| NFR-001  | Voice path: tool execution under ~300 ms p95 so the speech turn round-trip stays conversational.                                                                             |
| NFR-002  | All `submit_answer` writes are idempotent via Cosmos `ifMatch` (etag) conditional writes keyed on `(session_id, question_id)`. **Non-negotiable.**                           |
| NFR-003  | Question shuffling is reproducible and auditable: seed once at `start_quiz` (`seed = hash(session_id)`), persist seed + shuffled ID list in the session row.                  |
| NFR-004  | Per-question and per-quiz timers are server-side fields in the session row (`startedAt`, `questionStartedAt`, `timeLimitSeconds`). The model is never trusted to enforce time. |
| NFR-005  | Cosmos sessions partition by `/userId` for near-linear scale; TTL applied to stale/completed sessions per retention policy.                                                  |
| NFR-006  | AI Search starts at S1; scales up if the question bank exceeds 100k items.                                                                                                   |
| NFR-007  | Managed Identity is used for every platform service access (Cosmos, AI Search, Key Vault). Zero connection strings in code.                                                  |
| NFR-008  | Observability via Application Insights + Foundry tracing (thread/tool spans wired by Foundry Agent Service).                                                                 |
| NFR-009  | Every `submit_answer` emits a structured `grading_event`. App Insights stream carries `sessionId, questionId, userId, language, received, verdict, channel, scoreDelta, latencyMs, timestamp` — but NOT `expected` or `receivedRaw`, which persist only to the server-only Cosmos `audit` container. See [008-api-contracts §4.5](./008-api-contracts.md). |
| NFR-010  | Foundry Evaluations run **per language** and gate publishes of the question bank to catch drift, ambiguity, and per-language quality parity issues.                          |
| NFR-011  | Multilingual question bank stores **one record per `(question_logical_id, language)` pair** — not a single record with multiple translations.                                |
| NFR-012  | Production deployable end-to-end via `azd up` from a clean subscription using Bicep IaC.                                                                                     |
| NFR-013  | Realtime audio: voice session length is capped; conversational pacing avoids dead-air leakage (Realtime API is billed per audio minute).                                     |
| NFR-014  | Tool returns are TTS-friendly (sentence-length, no markdown/code blocks, options spoken as "Option A: …"; numerals expanded; phonetic-safe formatting for URLs/acronyms).    |

## 5. Out of Scope (v1)

- Voice biometrics, proctoring, secure-browser certification features.
- AI-generated questions (the bank is curated and static in v1).
- Adaptive/branching question selection.
- Multi-agent orchestration.
- Public exposure without API Management quotas (rate limiting is optional in v1 and **mandatory before public exposure**).

## 6. Future Enhancements (v2+)

| Enhancement              | Cost   | Notes                                                                                                                                                                       |
| ------------------------ | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AI-generated questions   | Cheap  | New `generate_question(topic, language)` tool + author-time review pipeline. Same data model.                                                                               |
| Adaptive testing         | Medium | State-in-Cosmos design accommodates; `next_question` selector becomes difficulty-aware. Multi-agent (quizmaster + difficulty-adjuster) earns its keep here — adopt LangGraph or MAF Workflows then. |
| Analytics dashboard      | Cheap  | Cosmos → Power BI or Fabric direct query. The `audit` container is already shaped for this.                                                                                 |
| Certification platform   | Medium | Adds proctoring, ID verification, secure browser, voice biometrics — these are policy/UX, not architectural blockers.                                                       |
| Additional languages     | Cheap  | Author + reindex. No code change.                                                                                                                                           |

## 7. Top Risks (surface up front)

1. **Answer leakage through LLM context.** Tool return shapes are a security boundary, not an implementation detail. Tests must assert `correct_answer` never appears in any string returned to the agent — across all languages. (See [005-security-model](./005-security-model.md), SEC-001/SEC-002.)
2. **Non-idempotent grading writes.** Without conditional writes, a network retry silently double-scores — especially likely on the voice channel where the network is flakier. Use Cosmos `ifMatch` etag on every `submit_answer`. (See NFR-002, SEC-006.)
3. **Per-language quality drift in the question bank.** Translating a question can change its difficulty, ambiguity, or correctness. Foundry Evaluations must run **per language** and gate publishes. (See NFR-010, TEST-011.)
