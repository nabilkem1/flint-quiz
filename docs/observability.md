# Observability — Flint Quiz

**Purpose**: The dimension policy, dashboard catalog, and runbook query references for the Flint Quiz agent. Every alert, workbook, and event in production traces back to one of the entries in this document.

**Owner**: Platform + Security. **Review cadence**: every change to `src/observability/`, every model upgrade, every new event added to the taxonomy.

**Cross-references**: [`specs/008-api-contracts.md §0.1 / §4.5.1`](../specs/008-api-contracts.md), [`specs/009-agent-governance.md`](../specs/009-agent-governance.md), [`infra/README.md §10`](../infra/README.md), [`tasks/008-observability.md`](../tasks/008-observability.md), [`docs/retention.md`](./retention.md), [`docs/llm-boundary.md`](./llm-boundary.md).

---

## 1. Emission Surface

Every structured event the agent emits routes through `src/observability/events.py`. The module enforces a dimension policy: required dimensions must be present; forbidden dimensions (any 🟡/🔴 field from `008-api §0.1`) refuse to emit at all.

The two emission paths:

1. **`emit_grading_event(...)`** — kwargs-only signature with the exact 10 fields from `008-api §4.5.1` (`session_id`, `question_id`, `user_id`, `language`, `received`, `verdict`, `channel`, `score_delta`, `latency_ms`, `timestamp`).
2. **`emit_agent_event(event, dimensions)`** — generic typed entry point for the `agent.*` taxonomy (TASK-149).

There is **no** untyped `emitter.emit("anything", {...})` in the production tool / agent / erasure paths. The dispatcher's existing emissions are bound to typed events at agent factory composition time.

---

## 2. Event Taxonomy

The complete catalog of structured custom events the agent emits. Names are stable contracts — alerts, workbooks, and runbook queries all reference these by name.

### 2.1 `grading_event`

| Field          | Tier | Notes                                          |
|----------------|------|------------------------------------------------|
| `session_id`   | 🟢   |                                                |
| `question_id`  | 🟢   |                                                |
| `user_id`      | 🟢   | Opaque Entra OID                               |
| `language`     | 🟢   |                                                |
| `received`     | 🟢   | Normalised option key (e.g., `B`), **not** raw |
| `verdict`      | 🟢   | `correct \| incorrect \| partial \| unanswered`|
| `channel`      | 🟢   | `text \| voice`                                |
| `score_delta`  | 🟢   |                                                |
| `latency_ms`   | 🟢   |                                                |
| `timestamp`    | 🟢   |                                                |

**Forbidden** on this event (asserted by `test_grading_event_emission.py` + `test_no_pii_in_telemetry.py`): `expected`, `received_raw`, `correct_answer`. Those live only in the Cosmos `audit` row (the two-sink contract from `008-api §4.5`).

### 2.2 `agent.*` / `audit.*` / `sweeper.*` taxonomy

| Event | Severity | Emitter | Dimensions |
|-------|----------|---------|------------|
| `agent.injection_detected`       | P2 (page on rate spike)         | Agent loop (GOV-061)                              | `session_id`, `language`, `channel`, `payload_hash` (SHA-256 + KV salt), `payload_encoding` (`plain\|base64\|rot13\|leet`), `redirect_class` (`soft\|hard`) |
| `agent.coverage_gap`             | P1 (alert if rate > 1% / 24h)   | `start_quiz` on `E_NO_COVERAGE` (GOV-025)         | `session_id`, `topic`, `requested_language`, `suggested_fallback`, `consent_path` (`pending\|accepted\|declined`) |
| `agent.refusal_loop`             | P1                              | Agent loop on 3× consecutive refusals (GOV-072)   | `session_id`, `language`, `channel`, `refusal_class` |
| `agent.unknown_tool`             | P1 (page on rate spike)         | Dispatcher (TASK-070 / GOV-010)                   | `session_id`, `requested_tool_name`, `principal_oid` |
| `agent.prompt_hash_mismatch`     | **P0**                          | Dispatcher (TASK-071 / GOV-003)                   | `session_id`, `expected_hash`, `actual_hash`, `language` |
| `agent.prompt_hash_missing`      | P1                              | Dispatcher (TASK-071)                             | `session_id`, `tool` |
| `agent.output_truncated`         | P2                              | Renderer (TASK-072 / GOV-091)                     | `session_id`, `language`, `channel`, `requested_max`, `returned` |
| `agent.auth_mismatch`            | P1                              | Dispatcher (GOV-063)                              | `tool`, `principal_oid_prefix` |
| `agent.tts_strip`                | P2 (info)                       | Voice TTS pipeline (TASK-108)                     | `session_id`, `language`, `stripped_chars` |
| `audit.user_erased`              | P2 (informational)              | GDPR cascade (TASK-134)                           | `pseudo_userid`, `requested_by`, `ticket_ref`, `counts.users`, `counts.sessions`, `counts.audit_pseudonymized` |
| `audit.user_erased.repeat`       | P3 (debug)                      | GDPR cascade no-op                                | `pseudo_userid`, `ticket_ref` |
| `audit.erasure_archive_locked`   | P2 (informational)              | GDPR cascade                                      | `pseudo_userid`, `locked_snapshot_ids[]` |
| `audit.erasure_denied`           | P1                              | GDPR cascade — group-membership rejection         | `principal_oid_prefix`, `reason` |
| `sweeper.stranded_released`      | P3 (debug)                      | Sweeper (003 TASK-191)                            | `count` |
| `sweeper.expired_swept`          | P3 (debug)                      | Sweeper                                           | `count` |
| `sweeper.paused_swept`           | P3 (debug)                      | Sweeper                                           | `count` |

**Hash discipline**: `payload_hash` on `agent.injection_detected` is SHA-256(utterance ⨁ salt) where salt lives in Key Vault (`injection-hash-salt`). The raw utterance is **never** in any event.

### 2.3 Span surface

Required spans (TASK-144):

| Span name                       | Required attributes              |
|---------------------------------|----------------------------------|
| `tool.list_topics`              | `language`, `channel`            |
| `tool.set_language`             | `language`, `channel`            |
| `tool.start_quiz`               | `language`, `channel`, `topic`   |
| `tool.submit_answer`            | `language`, `channel`, `verdict` |
| `tool.get_results`              | `language`, `channel`            |
| `cosmos.read`                   | `container`                      |
| `cosmos.conditional_write`      | `container`, `match_condition`   |
| `search.query`                  | `language`                       |
| `search.get_question_view`      | (timing only)                    |
| `search.get_answer_key`         | (timing only — **no** key data)  |

**Forbidden span attributes** (lint-enforced):

```
correct_answer, correctAnswer, answer_key, expected,
received_raw, receivedRaw, _etag
```

The lint runs at `tests/integration/test_span_lint.py` and walks every `.py` under `src/`. Any `span.set_attribute("correct_answer", …)` or `span.set_attributes({"correct_answer": …})` fails the build.

---

## 3. Dashboard Catalog

All workbooks live under `infra/modules/observability/workbooks/`.

| Workbook                           | Bicep module                       | Purpose                                                                 |
|------------------------------------|-------------------------------------|-------------------------------------------------------------------------|
| Quiz Voice — Hot Path              | `voice-hot-path.bicep`              | STT/TTS latency, voice tool-call round-trip per language (NFR-001).     |
| Quiz Correctness                   | `grading-correctness.bicep`         | Per-language / per-topic correctness %, chronically wrong questions.    |
| Quiz Cost                          | `cost.bicep`                        | Realtime audio minutes per session (NFR-013), tokens, RU, SU.           |
| Security & Governance              | `security-governance.bicep`         | 24h rate per `agent.*` event, P0 highlights, sweeper counts, GDPR audit.|

---

## 4. Alert Catalog

All alerts default **off in dev**, **on in prod**.

| Alert                                          | Bicep module                                  | Severity | Trigger                                                                          |
|-----------------------------------------------|------------------------------------------------|----------|-----------------------------------------------------------------------------------|
| Voice tool-call p95 > 300 ms                  | `alerts/latency.bicep` + `alerts/voice-latency.bicep` | P2      | Voice latency budget exceeded over 5 min (NFR-001).                              |
| Cosmos 429 rate > 1% sustained                | `alerts/latency.bicep`                         | P2       | Cosmos 429 metric over 5 min.                                                    |
| AI Search 503 rate > 0 sustained              | `alerts/latency.bicep`                         | P1       | Search degraded over 5 min.                                                      |
| Per-language correctness drift                | `alerts/per-language-correctness.bicep`        | P1       | Deviation > N percentage points from 7-day baseline (min sample size gate).      |
| `agent.prompt_hash_mismatch` ≥ 1              | `alerts/governance-events.bicep`               | **P0**   | Single occurrence — pages on-call immediately (GOV-003).                          |
| `agent.injection_detected` rate spike         | `alerts/governance-events.bicep`               | P2       | > 10× rolling 7-day baseline (GOV-061).                                          |
| `agent.coverage_gap` rate > 1% / 24h          | `alerts/governance-events.bicep`               | P1       | Content team triage (GOV-025).                                                   |
| `agent.unknown_tool` ≥ 1                      | `alerts/governance-events.bicep`               | P1       | Model drift signal (GOV-010).                                                    |

---

## 5. Saved Queries (Runbook Hooks)

Every symptom in [`specs/007-operational-runbook.md §9`](../specs/007-operational-runbook.md) has a saved query under `infra/modules/observability/saved-queries/runbook-hooks.bicep`:

| Runbook symptom                     | Saved-query name                                  |
|-------------------------------------|---------------------------------------------------|
| Double-scoring                      | `<prefix>-<env>-runbook-double-scoring`           |
| Voice latency spike                 | `<prefix>-<env>-runbook-voice-latency-spike`      |
| Wrong-language served               | `<prefix>-<env>-runbook-wrong-language`           |
| Answer key in agent text (P0)       | `<prefix>-<env>-runbook-answer-key-leak`          |
| Coverage gap surge by topic         | `<prefix>-<env>-runbook-coverage-gap-surge`       |

Plus the headline per-language correctness query under `infra/modules/observability/saved-queries/per-language-correctness.bicep`.

---

## 6. Connection String — Not a Secret

The App Insights connection string is sourced from `APPLICATIONINSIGHTS_CONNECTION_STRING` (env var, Bicep output). It is **not** a secret per Microsoft guidance — it identifies a workspace, not authorises writes (auth is via Managed Identity). The CI grep in `.github/workflows/security-grep.yml` bounds where the connection string can appear; routing it through Key Vault is explicitly **not** done (mirrored in [`docs/secrets.md`](./secrets.md)).

---

## 7. Retention

App Insights retention windows live in [`docs/retention.md`](./retention.md):

* General telemetry: 90 days hot, 730 days archive.
* `grading_event`: 90 days hot (correctness analysis window).
* Transcript-bearing customEvents: 30 days (PII / SEC-008).

---

## 8. CI Enforcement

The build fails on:

1. **Forbidden span attribute** — `test_span_lint.py` scans `src/` for `set_attribute("correct_answer", …)` etc.
2. **Event dimension drift** — `test_grading_event_emission.py` + `test_agent_event_emission.py` exercise every event with both happy + violation cases.
3. **Telemetry PII leak** — `test_no_pii_in_telemetry.py` runs end-to-end flows (`submit_answer`, GDPR cascade) and greps every emitted event for the forbidden field names.

Each of these is wired into the standard pytest run; no separate observability CI step is required.

---

## 9. Operational Notes

* Workbooks populate within 10 minutes of first event.
* Alerts route to the configured Action Group (`actionGroupIds` parameter on every alert module). Empty in dev (no paging); populated in prod.
* The dispatcher's existing event emissions (`agent.unknown_tool`, `agent.auth_mismatch`, `agent.prompt_hash_mismatch`, `agent.prompt_hash_missing`) emit via `_emitter.emit(...)`. Production wiring should route those calls through the typed `emit_agent_event` helper at agent-factory composition time so the policy gate runs on every emission.
* The grading event is emitted via the typed helper from `submit_answer._emit_grading` already (TASK-141 wired in 008-observability).
