# DEV-STORY PROMPT — TASK-008 OBSERVABILITY

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 6 — Observability
**Current Task Pack**: 008-observability (App Insights + Foundry tracing wiring, `grading_event` emission, voice + grading-correctness dashboards, span discipline, latency alerts, per-language correctness monitor, cost dashboard, incident runbook hooks, `agent.*` governance event taxonomy + Security & Governance workbook)
**Scope**: Wire telemetry, emit the structured operational events, stand up the dashboards and alerts that make every GOV-* escalation actionable.

## TASK REFERENCES

- `tasks/008-observability.md`
  - TASK-140 — App Insights + Foundry tracing wired
  - TASK-141 — `grading_event` structured event emission (NFR-009)
  - TASK-142 — Voice hot-path dashboard
  - TASK-143 — Grading-correctness dashboard
  - TASK-144 — Tracing span discipline
  - TASK-145 — Latency alerts
  - TASK-146 — Per-language correctness rate monitor
  - TASK-147 — Cost dashboard
  - TASK-148 — Incident runbook hooks
  - TASK-149 — `agent.*` governance event taxonomy + Security & Governance workbook
- Cross-pack dependencies:
  - `tasks/001-infrastructure.md` TASK-009, TASK-012
  - `tasks/003-cosmos-db.md` TASK-044, TASK-051, TASK-191
  - `tasks/004-agent-framework.md` TASK-070, TASK-071, TASK-072
  - `tasks/005-tools.md` TASK-084
  - `tasks/006-voice-realtime.md` TASK-107
  - `tasks/007-security.md` TASK-126, TASK-134

## SPEC REFERENCES

- `specs/008-api-contracts.md` — §0.1 (🟡/🔴 fields), §4.5.1 (grading_event dimensions)
- `specs/009-agent-governance.md` — GOV-003, GOV-010, GOV-025, GOV-052, GOV-060, GOV-061, GOV-072, GOV-091, §15 escalation table
- `specs/005-security-model.md` — SEC-001, SEC-008, SEC-014
- `specs/007-operational-runbook.md` — §2.3 (voice), §5 (cost), §9 (incidents)
- `specs/006-testing-strategy.md` — TEST-010, TEST-019, TEST-022, TEST-023, TEST-025, TEST-028

## ADR REFERENCES

- `adr/001-use-microsoft-agent-framework.md` — Foundry tracing
- `adr/005-tool-boundary-prevents-answer-leakage.md` — no `correct_answer` on any telemetry surface
- `adr/006-retention-policy.md` — LAW retention windows

## GOVERNANCE REFERENCES

- `docs/ai-agent-development-guidelines.md` — telemetry boundaries, dimension policy
- `docs/coding-standards.md` — span naming, dimension cardinality
- `docs/llm-boundary.md` — never log what shouldn't be there
- `docs/retention.md` — LAW workspace retention
- `infra/README.md` §10.2, §10.4, §11.2 — workbooks + dashboards + INF-101

## OBJECTIVE

Implement the observability layer that:

1. Initialises OpenTelemetry in the agent boot path via `azure-monitor-opentelemetry`; enables Foundry tracing for thread/tool spans.
2. Emits one `grading_event` per persisted answer with exactly the dimensions from `specs/008-api-contracts.md §4.5.1`; explicitly excludes `expected` and `receivedRaw` from the App Insights event (those live only in Cosmos `audit`).
3. Stands up the "Quiz Voice — Hot Path" workbook visualising STT first-final, TTS first-byte, voice tool-call round-trip (p50/p95/p99) per language.
4. Stands up the "Quiz Correctness" workbook visualising overall + per-language + per-topic correctness % and per-question verdict heatmap.
5. Defines and enforces the span set: `tool.{list_topics,set_language,start_quiz,submit_answer,get_results}`, `cosmos.{read,conditional_write}`, `search.{query,get_question_view,get_answer_key}`. Forbids `correct_answer` as a span attribute (CI lint).
6. Wires latency alerts: voice p95 > 300 ms over 5-min, Cosmos 429 rate > 1%, AI Search 503 rate > 0 sustained. Off by default in dev; on by default in prod.
7. Adds per-language correctness rate monitor with 7-day rolling baseline; alert when any language deviates > X% from baseline (triggers per-language Foundry Evaluation).
8. Adds cost dashboard (Realtime audio minutes per session, Foundry model tokens, Cosmos RU, AI Search SU).
9. Attaches App Insights queries / workbook links to every runbook entry in `specs/007-operational-runbook.md §9`.
10. Emits the `agent.*` governance event taxonomy and surfaces it on the "Security & Governance" workbook with per-event alerts matching `infra/README §10.2`.

## IMPLEMENTATION RULES

- **App Insights connection string is the documented exception** (not a secret per Microsoft guidance). Sourced from env, NOT from Key Vault.
- **`grading_event` dimensions (TASK-141 / `specs/008-api-contracts.md §4.5.1`)**: `sessionId`, `questionId`, `userId` (opaque Entra OID), `language`, `received` (normalized option key — NOT free text), `verdict`, `channel` (`text|voice`), `scoreDelta`, `latencyMs`, `timestamp`.
- **Explicitly excluded from App Insights `grading_event`**: `expected`, `receivedRaw`. Those live only in Cosmos `audit` (003 TASK-044). The exclusion is asserted by TEST-010.
- **`grading_event` emitted only on the successful `submit_answer` write branch.** Not on idempotent no-op. Not on sweeper auto-grade more than once per slot.
- **Span discipline (TASK-144)**:
  - Required spans: `tool.list_topics`, `tool.set_language`, `tool.start_quiz`, `tool.submit_answer`, `tool.get_results`, `cosmos.read`, `cosmos.conditional_write`, `search.query`, `search.get_question_view`, `search.get_answer_key`.
  - Required attributes (where applicable): `language`, `channel`, `verdict`.
  - **Forbidden attribute name**: `correct_answer` (CI lint fails the build on any span attribute matching).
- **Alerts** (TASK-145):
  - Voice tool-call p95 > 300 ms over 5-min → ticket.
  - Cosmos 429 rate > 1% → ticket.
  - AI Search 503 rate > 0 sustained → ticket.
  - Default off in dev; on in prod. Quiet hours configured in dev.
- **Per-language correctness monitor (TASK-146)**:
  - 7-day rolling correctness rate per language saved query.
  - Alert when one language deviates > X% from baseline; minimum-N sample gate before firing.
  - Triggers per-language Foundry Evaluation (009-testing TASK-167) on alert.
- **Cost dashboard (TASK-147)**: per-resource cost surface + KPI "Realtime audio minutes per session" (NFR-013 anchor); voice session length cap (006 TASK-105) visible alongside.
- **Incident runbook hooks (TASK-148)**: each symptom in `specs/007-operational-runbook.md §9` attaches a query / workbook link:
  - Double-scoring → duplicate `(sessionId, questionId)` query on `audit`.
  - Voice latency spike → voice workbook.
  - Wrong-language served → `start_quiz` events where session language ≠ user language without explicit override.
  - Answer key in agent text → P0; direct link to TEST-006 failures.
- **`agent.*` event taxonomy (TASK-149)** — canonical names + severities + dimensions (🟢 only — no PII, no answer keys):

  | Event | Severity | Emitter | Dimensions |
  |-------|----------|---------|------------|
  | `agent.injection_detected` | P2 (page on rate spike) | Agent loop (GOV-061) | `session_id`, `language`, `channel`, `payload_hash` (SHA-256 of utterance with KV salt), `payload_encoding` (`plain\|base64\|rot13\|leet`), `redirect_class` (`soft\|hard`) |
  | `agent.coverage_gap` | P1 (alert if rate > 1% / 24h) | `start_quiz` on `E_NO_COVERAGE` (GOV-025) | `session_id`, `topic`, `requested_language`, `suggested_fallback`, `consent_path` (`pending\|accepted\|declined`) |
  | `agent.refusal_loop` | P1 | Agent loop on 3 consecutive refusals (GOV-072) | `session_id`, `language`, `channel`, `refusal_class` |
  | `agent.unknown_tool` | P1 (page on rate spike) | Dispatcher (TASK-070 / GOV-010) | `session_id`, `requested_tool_name`, `principal_oid` |
  | `agent.prompt_hash_mismatch` | **P0** | Dispatcher (TASK-071 / GOV-003) | `session_id`, `expected_hash`, `actual_hash`, `language` |
  | `agent.output_truncated` | P2 | Renderer (TASK-072 / GOV-091) | `session_id`, `language`, `channel`, `requested_max`, `returned` |
  | `agent.user_erased` | P2 (informational) | Erasure cascade (TASK-134) | `pseudo_userid`, `requested_by`, `ticket_ref`, `counts.users`, `counts.sessions`, `counts.audit_pseudonymized` |
  | `agent.user_erased.repeat` | P3 (debug) | Erasure cascade no-op | `pseudo_userid`, `ticket_ref` |
  | `audit.erasure_archive_locked` | P2 (informational) | Erasure cascade | `pseudo_userid`, `locked_snapshot_ids[]` |
  | `sweeper.{stranded_released,expired_swept,paused_swept}` | P3 (debug) | Sweeper (003 TASK-191) | `count` |

- **Hash discipline**: `payload_hash` on `agent.injection_detected` is SHA-256 with service-wide salt from Key Vault (`injection-hash-salt`). Raw utterance **never** in the event. CI lint forbids any field name overlapping `specs/008-api-contracts.md §0.1 🟡/🔴`.
- **Security & Governance workbook (TASK-149)** Bicep module: `infra/modules/observability/workbooks/security-governance.bicep`. Tiles: 24-hour rate per event name; top 10 `topic`s with `agent.coverage_gap`; `agent.injection_detected` by `payload_encoding`; `agent.prompt_hash_mismatch` highlight (P0); `sweeper.*` counts/hour.
- **Alerts matching `infra/README §10.2`**:
  - `agent.prompt_hash_mismatch` ≥ 1 → P0 page.
  - `agent.injection_detected` rate > 10× rolling baseline → P2 page.
  - `agent.coverage_gap` rate > 1% of `start_quiz` / 24h → P1 ticket (content team).
  - `agent.unknown_tool` ≥ 1 → P1 ticket (model drift).

## OUTPUT FILES

Generate:

- `src/observability/__init__.py`
- `src/observability/telemetry.py` — OpenTelemetry init via `azure-monitor-opentelemetry`; Foundry tracing enable
- `src/observability/events.py` — typed event emitters: `emit_grading_event`, `emit_agent_event(event_name, dimensions)`, etc., with CI-linted dimension policy
- `src/observability/spans.py` — span helpers + `forbidden_attribute_lint` check
- `src/observability/cost.py` — cost dashboard helpers (per-resource queries)
- `infra/modules/observability/workbooks/voice-hot-path.bicep`
- `infra/modules/observability/workbooks/grading-correctness.bicep`
- `infra/modules/observability/workbooks/cost.bicep`
- `infra/modules/observability/workbooks/security-governance.bicep`
- `infra/modules/observability/alerts/latency.bicep`
- `infra/modules/observability/alerts/per-language-correctness.bicep`
- `infra/modules/observability/alerts/governance-events.bicep` (P0 prompt-hash; P2 injection-rate; P1 coverage-gap-rate; P1 unknown-tool)
- `infra/modules/observability/saved-queries/per-language-correctness.bicep`
- `infra/modules/observability/saved-queries/runbook-hooks.bicep` — one saved query per runbook §9 entry
- `tests/integration/test_grading_event_emission.py` — TEST-010 (event count, dimensions, exclusions)
- `tests/integration/test_span_lint.py` — `correct_answer` as span attribute fails the build
- `tests/integration/test_agent_event_emission.py` — every event in the taxonomy emits with correct dimensions
- `tests/integration/test_no_pii_in_telemetry.py` — AL-006 redaction (no 🟡/🔴 fields in App Insights events)
- `docs/observability.md` (or extend) — dimension policy + dashboard catalog + runbook query references

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **NFR-008**: end-to-end transaction view shows agent → tool → Cosmos/Search spans.
- **NFR-009 / TEST-010**: one `grading_event` per persisted answer; all required dimensions present; `expected` and `receivedRaw` absent from App Insights event; present in matching `audit` row.
- **SEC-001 in telemetry**: no `correct_answer` field, attribute, or substring anywhere in App Insights surface.
- **NFR-001 alert**: synthetic voice latency spike fires the voice alert.
- **GOV-003 / TEST-025**: `agent.prompt_hash_mismatch` event fires on mid-session mutation; P0 alert wired.
- **GOV-010 / TEST-019**: `agent.unknown_tool` event fires on rejected dispatch.
- **GOV-025 / TEST-022**: `agent.coverage_gap` event fires with `consent_path` reflecting the user's decision.
- **GOV-060/061 / TEST-023**: `agent.injection_detected` event fires; payload field is SHA-256 hash, never raw text.
- **GOV-091**: `agent.output_truncated` event fires on over-length runs.
- **TEST-028 / TASK-134**: `agent.user_erased` event present with correct counts after the GDPR cascade.
- **Workbooks**: each workbook populates within 10 minutes of first event.
- **CI lint**: any span attribute or event field named `correct_answer` fails the build.

## FORBIDDEN ACTIONS

- Do NOT include `expected`, `receivedRaw`, `correct_answer`, or any 🟡/🔴 field from `specs/008-api-contracts.md §0.1` in any App Insights event, span attribute, or log line.
- Do NOT emit `grading_event` on the idempotent no-op return path of `submit_answer`. Doubles the metric; TEST-007 verifies.
- Do NOT log raw user utterances. `received` is the normalized key; `receivedRaw` lives only in Cosmos `audit`.
- Do NOT include the raw `injection_detected` payload in the event. SHA-256 hash with KV salt only.
- Do NOT include user PII in any dashboard tile or saved query. Pseudonymize on the erasure path (007 TASK-134) and document the boundary.
- Do NOT store the App Insights connection string in Key Vault. It is not a secret per Microsoft guidance.
- Do NOT bypass the dimension policy. Every event has exactly the dimensions documented; CI lint enforces.
- Do NOT register an alert that would page on a single noisy event (e.g., `agent.injection_detected` ≥ 1 would page on every legitimate adversarial test). Use rate-based thresholds with rolling baselines.
- Do NOT register dev-environment alerts that page on-call. Default off in dev; on in prod.
- Do NOT add saved queries that join `audit` (PII surface) with App Insights (analytics surface). The boundary is intentional.
- Do NOT extend spans into the answer-key fetch path with attributes that reveal the key. `search.get_answer_key` span carries timing only.
- Do NOT implement the GDPR cascade, the prompt-injection corpus, the dispatcher, or the sweeper in this pack — those live in 007, 007, 004, 003 respectively. This pack instruments them.
- Do NOT skip the per-language correctness monitor. Per-language correctness is the headline metric for an exam system, not uptime.
