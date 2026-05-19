# 007 — Operational Runbook

- **Version**: v1.0
- **Last reviewed**: 2026-05-17
- **Owner**: Platform on-call
- **Status**: Accepted

## 1. Resources to Provision

| Path                            | Purpose                                                                                                                                                          |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `infra/main.bicep`              | `azd`-deployable IaC: Foundry project, Hosted Agent, Cosmos, AI Search, Key Vault, App Insights, Storage, Realtime endpoint.                                     |
| `infra/main.parameters.json`    | Environment-specific knobs (supported languages, model name).                                                                                                    |
| `azure.yaml`                    | `azd` config.                                                                                                                                                    |
| `src/agent/quiz_agent.py`       | MAF agent definition, per-language phrasing blocks, tool registration, Realtime config.                                                                          |
| `src/agent/composition.py`      | DI composition root: constructs concrete dependencies at startup and wires them into the agent and tools.                                                         |
| `src/agent/tools.py`            | The five tools with strict TTS-friendly return shapes.                                                                                                           |
| `src/agent/answer_normalizer.py`| Spoken-input → option-key normalization (multilingual).                                                                                                          |
| `src/agent/tts_shaper.py`       | TTS-safe rendering helper (defense in depth before the channel adapter's defensive stripper).                                                                     |
| `src/agent/renderer.py`         | Single code path that converts tool results / `ErrorEnvelope` → LLM-visible string. SEC-001 enforcement point for error paths (008-api §6.4).                     |
| `src/agent/prompts/`            | Layered prompt files: `identity.txt`, `contract.txt`, `lang/{en,fr,es}.yaml`, `compose.py` (computes the SHA-256-pinned composed prompt per GOV-001/003).         |
| `src/common/`                   | Cross-cutting modules — owned by Platform, no domain logic: `config.py` (frozen Pydantic BaseSettings), `exceptions.py` (`FlintError` hierarchy — see [008-api §6.4](./008-api-contracts.md)), `logging_setup.py` (structured logging + redaction), `clock.py` (injected for testability), `telemetry.py` (OTel + App Insights wiring). |
| `src/data/cosmos_repository.py` | Session/user/audit reads + conditional writes.                                                                                                                   |
| `src/data/question_search.py`   | AI Search queries (filtered + seeded random draw, language filter, answer-key fetch is a separate server-only method).                                            |
| `src/data/keyvault_client.py`   | Thin Key Vault wrapper (MI-bound `SecretClient`, in-process TTL cache); the sole sanctioned path to secrets.                                                      |
| `src/data/erasure.py`           | GDPR right-to-erasure cascade (TASK-134); invoked by support tooling, not exposed as a tool.                                                                      |
| `src/data/migrations/`          | Versioned `schemaVersion` migrators for Cosmos documents per [008-api §6.5](./008-api-contracts.md).                                                              |
| `src/data/models.py`            | Pydantic models for `QuestionView`, `AnswerKey`, `SessionDoc`, `Answer`, `ResultsSummary`, `UserDoc`, `TopicDoc`, `AuditEvent`.                                   |
| `src/seed/questions/{en,fr,es}/*.json` | Authoring source for the initial multilingual bank.                                                                                                       |
| `src/seed/seed_index.py`        | One-shot loader: blob/JSON → AI Search index (per-language analyzers).                                                                                            |
| `pyproject.toml`                | Dependencies: `agent-framework`, `azure-ai-projects`, `azure-cosmos`, `azure-search-documents`, `pydantic`, `azure-monitor-opentelemetry`, `azure-keyvault-secrets`. |
| `.pre-commit-config.yaml`       | Pre-commit hooks: `ruff`, `black`, `mypy`, `detect-secrets`, `commitlint` per [docs/coding-standards.md §1.4](../docs/coding-standards.md).                       |

Test files are listed in [006-testing-strategy](./006-testing-strategy.md) §2.

## 2. Observability

### 2.1 Standard Telemetry

- **Application Insights** + **Foundry tracing** — already wired by Foundry Agent Service for thread/tool spans. (NFR-008)

### 2.2 Custom Metric — Grading Correctness Events

Every `submit_answer` emits a structured event with all the dimensions needed to track correctness over time. **System uptime is not the metric that matters here — grading correctness is.** (NFR-009)

The event is written to two sinks with different shapes (see [008-api-contracts §4.5](./008-api-contracts.md)):

- **App Insights `grading_event` (broad-access)** — `sessionId · questionId · userId · language · received · verdict · channel (text|voice) · scoreDelta · latencyMs · timestamp`. **No `expected`, no `receivedRaw`** — those would widen the answer-key trust boundary beyond the server-only `audit` container.
- **Cosmos `audit` row (server-only, RBAC-restricted)** — additionally carries `expected` (answer key, 🟡) and `receivedRaw` (raw user utterance, PII). Used for dispute resolution under the analyst/auditor role only.

Dashboards that need `expected` join `audit` to `grading_event` at query time. Verified by TEST-010 + AL-006.

### 2.3 Voice-Specific Dashboard

Separate dashboard tracking the voice hot path:

- STT latency.
- TTS latency.
- Tool-call round-trip in voice mode.

This is decoupled from the general grading-correctness dashboard because the voice path has different SLOs (NFR-001: tool execution under ~300 ms p95).

### 2.4 Foundry Evaluations (Out of Hot Path)

- Question-bank quality evaluations: drift, ambiguity, answer-key correctness over time.
- **Per language** (NFR-010), gating publishes.
- Not in the hot path; run on schedule or on author-time publish.

## 3. Reliability

- **Idempotency** (NFR-002, SEC-006): `submit_answer` uses Cosmos `ifMatch` (etag) conditional writes keyed on `(session_id, question_id)`. **Non-negotiable.**
- **Retries**: SDK-level retry on transient failures (Cosmos 429, Search 503). Tool-level retries are idempotent by construction.
- **Timeouts**: per-question + per-quiz server-side timers in the session row (NFR-004).
- **Circuit-breaker (optional)**: for Search if it degrades — degrade to "session frozen, resume later" rather than serve a wrong question.

## 4. Scalability

- **Cosmos**: partition by `/userId` for sessions → near-linear scale; autoscale RU/s; TTL on completed sessions older than retention policy. (NFR-005)
- **AI Search**: question bank is read-heavy + small → start S1, scale up if bank > 100k items. (NFR-006)
- **Foundry Hosted Agent**: managed auto-scale; the voice channel scales separately on the Realtime endpoint.

## 5. Cost Optimization

- Choose the smallest model that grades reliably (a smaller Foundry model — e.g., gpt-4.1-mini equivalent — is fine since the LLM isn't doing the grading).
- Keep tool returns small (text + options only, no rich metadata to LLM context).
- Cosmos: TTL stale sessions; reserved capacity once steady-state RU is known.
- Cache `topics` list (small, slow-changing) in App Configuration with a polling reload.
- **Realtime API**: bill is per-minute of audio — design conversational pace to avoid dead-air leakage; cap voice session length. (NFR-013)

## 6. Rate Limiting

- API Management front of the Hosted Agent endpoint with per-user quotas (questions/minute, quizzes/day, voice-minutes/day). (SEC-011)
- **Optional in v1; mandatory before public exposure.**

## 7. Implementation Phasing

| Phase                                | Duration   | Deliverables                                                                                                                                                                                       |
| ------------------------------------ | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Phase 1 — PoC core**               | 2–3 days   | Bicep skeleton, one topic, 10 hand-written questions × 3 languages, MAF agent + 5 tools, Cosmos sessions, AI Search index, runs in Foundry Playground text mode.                                  |
| **Phase 2 — Voice + hardening**      | 3–4 days   | Realtime endpoint wiring, answer normalizer, TTS-friendly tool returns, conditional-write idempotency, answer-leakage tests, grading observability, Managed Identity end-to-end, App Insights wiring. |
| **Phase 3 — Operational polish**     | 2–3 days   | Foundry Evaluations per language, retention policy + TTL, rate limiting via APIM, runbook, cost dashboard, channel-switch test.                                                                    |
| **Phase 4 — Optional v2**            | —          | Adaptive testing → LangGraph or MAF Workflows; AI-generated questions; analytics dashboard; certification-platform features.                                                                       |

## 8. Operational Checklists

### 8.1 Pre-Deploy Checklist

- [ ] All resources defined in `infra/main.bicep`.
- [ ] `infra/main.parameters.json` set for the target environment.
- [ ] Managed Identity role assignments verified (Cosmos Data Contributor, Search Index Data Reader, Key Vault Secrets User).
- [ ] App Configuration populated (model name, search endpoint, supported languages).
- [ ] Key Vault populated with any required secrets.

### 8.2 Post-Deploy Smoke

- [ ] TEST-001 — `azd up` deploys cleanly.
- [ ] TEST-002 — seed loader populates AI Search across 3 languages × 3 topics.
- [ ] TEST-003/004/005 — text English, text French, voice Spanish smoke tests pass.
- [ ] TEST-010 — App Insights shows grading events with all dimensions.

### 8.3 Per-Release Quality Gate

- [ ] All automated tests in `tests/` pass.
- [ ] TEST-011 — per-language Foundry Evaluation passes parity tolerance.
- [ ] Answer-leakage test (TEST-006) passes for every supported language.

### 8.4 Pre-Public-Exposure Gate

- [ ] API Management quotas configured (SEC-011).
- [ ] Retention policy applied to `sessions` (TTL) and transcripts.
- [ ] Compliance review of the "what does the LLM see" boundary (SEC-009).

## 9. Operational Runbook — Incidents

| Symptom                                                                    | First check                                                                                            |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Double-scoring reports from users                                          | Confirm `submit_answer` is using etag conditional writes (NFR-002); inspect `audit` events.            |
| Voice latency spike                                                        | Voice dashboard: STT/TTS/tool-call round-trip. Confirm no Evaluations in hot path.                     |
| Wrong-language questions served                                            | Confirm `start_quiz` filtered AI Search by `language`; inspect `users.language` and `sessions.language`. |
| Answer key showing in agent text                                           | **P0 incident**: TEST-006 regression. Roll back tool layer; re-run leak test across all languages.     |
| Per-language question-bank drift flagged by evaluation                     | Block publish; author fix; re-evaluate (NFR-010).                                                      |

## 10. Glossary

Authoritative definitions of operational terms used across specs and standards:

| Term | Definition |
|------|------------|
| **Hot path** | Code path invoked per-user-turn, bound by NFR-001's 300 ms p95 voice tool-call budget. Foundry Evaluations, archive jobs, and the sweeper are **not** hot-path. |
| **Cold path** | Anything not on the hot path: per-language Foundry Evaluations, the audit-archive job (TASK-051), the session sweeper (TASK-191), nightly cost rollups. |
| **Sweeper** | Background job (TASK-191) that flips silently-abandoned `Active` sessions to `Expired` after `voice:maxStrandedSeconds` (default 300 s) for stranded-at-index-0 sessions, or after the per-quiz timer elapses for in-progress sessions. Owner: Platform. Cadence: every 60 s. |
| **Stranded session** | An `Active` session with `currentIndex == 0` (no `submit_answer` traffic) for more than `voice:maxStrandedSeconds`. Reclaimed by the sweeper. |
| **`sessions:pauseThresholdSeconds`** | App Configuration key (default 300 s) that drives the `Active → Paused` heartbeat transition in spec 008 §4.3. Owner: Platform. |
| **Channel adapter** | The per-channel I/O surface — Foundry Playground for text, Foundry Realtime endpoint for voice — that adapts the native channel protocol to the shared agent loop. Owns transport-level shaping (defensive TTS strip, partial-transcript suppression) but **never** business logic. |
| **Renderer** | The single code path (`src/agent/renderer.py`) that converts a tool result (success `data` or `ErrorEnvelope`) into the string that enters LLM context. The renderer is the load-bearing enforcement point for SEC-001 on error paths — see [008-api-contracts §6.4](./008-api-contracts.md). |
| **Audit-of-audit** | A pseudonymized event recording that an erasure cascade ran — itself an audit record. See ADR 006 §3 and TASK-134. |

## 11. Dispute Resolution

When a user disputes a grading verdict:

1. **Agent path (GOV-103)** — the agent's only allowed response is to acknowledge in the active language using the phrasing block's `DisputeAcknowledge` line and offer the support path: "your session is recorded in our audit log; contact support with your session ID to dispute." The agent does NOT retrieve the audit row, does NOT re-grade, does NOT re-submit `submit_answer` ([009-gov GOV-101](./009-agent-governance.md)).
2. **Support path** — the dispute owner (support role) opens the `audit` container (RBAC-restricted) and inspects the `(sessionId, questionId)` row:
   - `expected` (🟡, present in audit only — never in App Insights or `sessions`).
   - `receivedRaw` (🟢 in audit; transcript-retention bound).
   - `received` (normalized).
   - `verdict` and `scoreDelta`.
3. **For disputes older than the Cosmos `audit` hot window** (365 days per ADR 006), retrieve the row from the `audit-archive` Blob container; immutability is locked, so the original `userId` (or pseudonymized `userId` if the user has since been GDPR-erased) is preserved.
4. **Resolution authority**: a verified grader-correctness defect produces a content-team ticket against the question bank + a refund of `scoreDelta` for affected sessions via a one-off operator script. The script touches `sessions` rows directly with `ifMatch` etag; it is NOT exposed as a tool.

## 12. References

- Microsoft Agent Framework GA — [devblogs.microsoft.com/foundry](https://devblogs.microsoft.com/foundry/microsoft-agent-framework-reaches-release-candidate/)
- Prompt Flow retirement — [techcommunity.microsoft.com](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/prompt-flow-is-being-retired/4513587)
- Foundry Agent Service overview — [learn.microsoft.com](https://learn.microsoft.com/en-us/azure/foundry/agents/overview)
- MAF threads & state — [learn.microsoft.com/agent-framework](https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/multi-turn-conversation)
- Cosmos DB integration with Foundry Agent Service — [learn.microsoft.com](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/azure-agent-service)
- LangGraph + Foundry Agent Service — [learn.microsoft.com](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-agents)
