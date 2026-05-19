# ADR 006 — Retention Policy: Two-Stage Audit + Bounded Session TTL

- **Status**: Accepted
- **Date**: 2026-05-17
- **Last reviewed**: 2026-05-17

## Context

Three retention windows needed to be set:

1. **Active and completed session rows** (`sessions` Cosmos container).
2. **Audit log** of grading events (`audit` Cosmos container) — evidentiary, dispute-bearing.
3. **PII-bearing transcripts** (App Insights / Foundry tracing) — user utterances, voice transcripts.

The audit ([docs/spec-audit-report.md §1.1](../docs/spec-audit-report.md)) found three specs disagreeing on these numbers:

- `008-api-contracts.md §2.1` said 90 days for `sessions` TTL.
- `tasks/003-cosmos-db.md` TASK-050 said 30 days.
- `infra/README.md §12.1` said 30 days.
- Audit retention: 180 / 365 / 7 years across three documents.

We needed one ADR-grade decision and a documented enforcement path.

## Decision

### Session retention — 30 days hot, hard-delete

- `sessions` Cosmos TTL = **30 days** (configurable via AppConfig `retention:sessionsScoredDays`).
- TTL is set on transition to `Scored` or `Expired` (per `tasks/003-cosmos-db.md` TASK-050).
- No archive: completed sessions are not evidentiary by themselves — the `audit` container is the system of record.

### Audit retention — two-stage (365 days hot Cosmos + 7 years immutable Blob)

- Cosmos `audit` TTL = **365 days hot** (configurable via AppConfig `retention:auditHotDays`).
- Daily archive job copies rows approaching TTL to `audit-archive` Blob container with **time-based immutability** for **7 years** (per `tasks/003-cosmos-db.md` TASK-051).
- Archive job is idempotent; archives rows with `_ts + ttl - 30 days <= now()` (30-day lead time before Cosmos delete).
- 7-year window is the compliance/dispute window for an exam system; can be lengthened by ADR amendment if a regulated tenant requires longer.

### Foundry Evaluation result archival — 365 days hot + 7 years immutable

Per-language Foundry Evaluation results (NFR-010, TEST-011) gate publishes and are evidentiary for content-team appeals on a gated publish.

- **Hot storage** — Foundry Evaluation run records retained in the Foundry project for **365 days** (configurable via AppConfig `retention:evalResultsHotDays`).
- **Archive** — daily archive job copies completed evaluation results to `eval-archive` Blob container with time-based immutability for **7 years** (matches `audit-archive` window for consistency).
- **Appeal process** — a content-team appeal against a gated publish references the specific evaluation run ID. The run record (questions, scores, model used, eval prompt version) is the evidence. Beyond the 7-year window, appeals are not supported.
- **Owner** — Product (appeal process); Platform (archive job + retention).

### Transcript retention — 30 days

- App Insights / Foundry tracing retains user-utterance-bearing customEvents for **30 days** (configurable via AppConfig `retention:transcriptDays`).
- Beyond 30 days, transcripts are PII-scrubbed; the structural shape of telemetry (event names, counts, latencies) is retained per Log Analytics retention (90 days hot in prod, 730 archive).

### GDPR right-to-erasure cascade

- Hard-delete `users.{userId}`; hard-delete all `sessions` partitioned by that `userId`.
- **Pseudonymize** `userId` in `audit` (and `audit-archive` snapshots not yet immutability-locked) — audits survive user erasure.
- Cascade implemented in `tasks/007-security.md` TASK-134 (new in this audit pass).

## Rationale

- **Cosmos at 7 years is cost-prohibitive** for per-event audit volume. Immutable Blob is ~1/100 the cost per GB and satisfies evidentiary needs.
- **Hot Cosmos retention (365 d)** is the right window for live dispute triage and dashboard queries that join `audit` to `grading_event`. After 365 days, disputes are read-only (no new event correlations expected) and Blob's append-only model is appropriate.
- **Session TTL (30 d)** is short because `sessions` rows are operational state, not evidence. The audit trail of what happened during those sessions lives in `audit`, which outlives the session row by orders of magnitude.
- **Transcript 30 d** is the shortest window that supports voice-quality monitoring + early-warning content-team triage; beyond that, the marginal forensic value of raw utterances is dominated by the PII risk.
- **Two-stage audit** mirrors how compliance systems are typically built (hot DB + cold object store) and minimises the time the answer-key-shaped `expected` field sits in a queryable surface. (`expected` is server-only; see [`docs/llm-boundary.md`](../docs/llm-boundary.md).)

## Alternatives Considered

| Approach | Why not |
|----------|---------|
| 90-day Cosmos TTL on `sessions` | Three docs disagreed; `infra/README` and `tasks/003` already aligned on 30 d. 90 d would increase Cosmos cost without operational value (the audit container holds the evidence). |
| Single-stage `audit` retention (7 years all in Cosmos) | Cost-prohibitive for per-event volume; Cosmos is overkill for cold evidentiary read access. |
| Audit retention in Cosmos 180 days, no archive (per old `tasks/003` TASK-051) | Insufficient compliance window for an exam system; disputes can surface months after the event. |
| Audit retention 7 years all in Cosmos | See above. |
| Per-user-controlled retention (user can request earlier deletion) | GDPR right-to-erasure (above) covers this without a per-user setting. Adding a setting would multiply the test matrix without compliance benefit in v1. |

## Consequences

### Positive

- One source of truth for each retention window: AppConfig.
- Cosmos cost bounded by 30-day session TTL + 365-day audit TTL (rolling).
- Compliance-grade evidentiary archive (Blob immutability) without paying Cosmos cost for cold data.
- GDPR cascade is well-defined and does not destroy the audit trail.

### Negative / Trade-offs

- Two storage tiers for audit data — a daily job is the bridge. Implementation surface in `tasks/003 TASK-051`.
- Cross-tier queries (e.g., joining a 2-year-old dispute against the live grading dashboard) require reading from Blob — slower, but rare. Acceptable.
- Adjusting any retention window requires this ADR's amendment (per `infra/README §12.2` rule 5) — friction is intentional.

### Revisit When

- A tenant with regulatory retention > 7 years onboards.
- Cosmos pricing changes such that single-stage audit becomes economic at 7 years.
- A new data class is introduced (e.g., AI-generated questions in v2 might bring author-provenance retention into scope).

## Container of Truth

The authoritative retention table lives in [`docs/retention.md`](../docs/retention.md). This ADR captures **why** those numbers; the docs/ file captures **what** to enforce. The ADR is the gate for changes; the docs file is the daily reference.

## Verification

- `tasks/003 TASK-050` + integration test: session TTL applied on terminal transition.
- `tasks/003 TASK-051` + integration test: audit TTL + archive job correctness.
- `tasks/007 TASK-132`: post-deploy assertion script verifying LAW retention.
- `tasks/007 TASK-133`: assertion that `audit-archive` Blob immutability policy is locked.
- `tasks/007 TASK-134` (new): GDPR cascade tested end-to-end.
- Pre-public-exposure gate (`docs/pre-public-gate.md` §2.6): every retention row has an enforcing test.

## Links

- [docs/retention.md](../docs/retention.md) — daily-reference table.
- [specs/005-security-model.md §7](../specs/005-security-model.md) — PII discipline.
- [specs/008-api-contracts.md §2.1](../specs/008-api-contracts.md) — `sessions.ttl` field.
- [specs/008-api-contracts.md §2.4](../specs/008-api-contracts.md) — `audit` container TTL + archive.
- [infra/README.md §12](../infra/README.md) — infrastructure retention rules.
- ADR [003-use-cosmos-db-for-session-state](./003-use-cosmos-db-for-session-state.md).
