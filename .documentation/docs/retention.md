# Retention Policy — Flint Quiz

**Purpose**: Documented, enforced retention windows for every data class. The pre-public-exposure gate requires this document to be current and reviewed ([`docs/pre-public-gate.md`](./pre-public-gate.md)).

**Owner**: Platform + Security. **Review cadence**: every policy change requires an ADR ([`infra/README §12.2`](../infra/README.md) rule 5).

**Cross-references**: [`specs/005-security-model.md §7`](../specs/005-security-model.md) (PII), [`specs/008-api-contracts.md §2`](../specs/008-api-contracts.md) (per-container TTL), [`tasks/003-cosmos-db.md` TASK-050/051](../tasks/003-cosmos-db.md), [`tasks/007-security.md` TASK-132/133](../tasks/007-security.md), [`infra/README.md §12`](../infra/README.md).

---

## 1. Policy Table

| Data class | Store | Hot retention | Cold / archive | Disposition | Driver |
|------------|-------|---------------|----------------|-------------|--------|
| Active session in progress | Cosmos `sessions` (`status IN {Active, Paused}`) | Until terminal state | — | Transitions to terminal state via tool path or sweeper | NFR-004, GOV-080 |
| Completed / scored sessions | Cosmos `sessions` (`status IN {Scored, Expired}`) | **30 days** (TTL) | — | Hard delete | NFR-005, SEC-008. Matches `008-api §2.1` and `infra/README §12.1`. |
| User profile | Cosmos `users` | Indefinite (until GDPR right-to-erasure) | — | Hard delete on deletion request; pseudonymize in `audit` | SEC-008, GDPR (audit P2 §5.7) |
| Audit log (grading events) | Cosmos `audit` | **365 days** hot | **7 years** in immutable Blob (`audit-archive`) | Auto-purge from Cosmos via TTL; archived to Blob by daily job; Blob expires at 7y | SEC-014, dispute window. Two-stage policy per audit Fix-3. |
| Authoring source-of-truth | Blob `authoring` | Indefinite | Versioned (`Blob versioning` enabled) | n/a | Reindexability; rollback path (`docs/rollback.md`) |
| Indexed question bank | AI Search `questions` index | Rebuildable from `authoring` | — | n/a | Re-indexable; not a system of record |
| App Insights telemetry (general) | Log Analytics workspace | 90 days hot | 730 days archive | Auto-purge | NFR-008; cost discipline |
| App Insights `grading_event` | Log Analytics workspace | 90 days hot | 730 days archive | Auto-purge | NFR-009; correctness analysis window |
| Voice transcripts | Foundry tracing → App Insights | 30 days | — (PII-scrubbed beyond) | Auto-purge | SEC-008 (PII). `infra/README §12.1`. |
| Text transcripts | Foundry tracing → App Insights | 30 days | — (PII-scrubbed beyond) | Auto-purge | SEC-008 (PII) |
| Activity log (control-plane changes) | LAW + Storage (immutable, prod) | 2 years | — | Auto-purge | Compliance, forensic |
| Key Vault secret history | Key Vault (soft-delete + purge protection) | 90 days post-deletion | — | Auto-purge | SEC-013 |
| Cosmos PITR backups | Cosmos | 30 days | — | Auto-purge | DR (`infra/README §9`) |
| Blob immutable copies (audit) | `audit-archive` container | n/a | 7 years (time-based immutability) | Auto-expire at 7y | SEC-014 |

---

## 2. Two-Stage Audit Retention

The reconciliation from audit Fix-3:

```
audit (Cosmos)                  audit-archive (Blob, immutable)
─────────────────                ─────────────────────────────────
day 0 ──────────────── day 365  | (archived ≥ day 335) ─────── day 2555 (7y)
            hot, queryable      | cold, evidentiary, immutable
```

- **Hot (Cosmos, 365 d)**: queryable, joined with App Insights dashboards for dispute triage. Per-row Cosmos TTL.
- **Daily archive job**: archives rows with `_ts + ttl - 30 days <= now()` (30-day lead time before Cosmos delete) to immutable Blob. Idempotent (re-running over an already-archived row is a no-op). Implemented by `tasks/003 TASK-051`.
- **Cold (Blob, 7 y)**: time-based immutability policy. Cannot be deleted before expiry. Read by analysts via separate UAMI (`uami-monitor-*` or a dedicated audit-reader identity).

Why two stages: Cosmos at 7-year retention is cost-prohibitive for the per-event volume. Immutable Blob is ~1/100 the cost per GB and satisfies the evidentiary requirement.

---

## 3. GDPR Right-to-Erasure Cascade

Per `infra/README §12.2 rule 4` (and audit P2 §5.7 — outstanding task):

1. **Mark `users.{userId}`** as deleted (soft-delete with legal-hold retention).
2. **Hard-delete all `sessions`** for that `userId` (partition-scoped, single delete query by partition).
3. **Pseudonymize `audit` rows**: replace `userId` with a pseudonym tag (`pseudo:{hash(userId,salt)}`). **Do not delete** — audits are evidentiary and survive user erasure for the audit-retention window.
4. Pseudonymize the same `userId` in Blob `audit-archive` snapshots that have not yet hit immutability lock. (Snapshots already locked cannot be modified; the pseudonym applies prospectively. Document this in the user-facing erasure response.)
5. Emit an `audit.user_erased` event (the audit of the audit) — pseudonymized record.

The cascade is implemented as a separate maintenance flow, not exposed as a user-facing tool. It is invoked by a support role on request, gated by Entra group membership + ticket reference.

---

## 4. Configurability via App Configuration

Retention windows are sourced at deploy time from `infra/main.parameters.<env>.json` and per-run from AppConfig:

| AppConfig key | Default | Effect |
|---------------|---------|--------|
| `retention:sessionsScoredDays` | `30` | TTL set on `sessions` row on terminal-state transition |
| `retention:auditHotDays` | `365` | Cosmos TTL on `audit` rows |
| `retention:auditArchiveYears` | `7` | Blob `audit-archive` immutability window |
| `retention:transcriptDays` | `30` | App Insights query for transcript-bearing customEvents purge |
| `retention:lawHotDays` | `90` (dev/qa: 30) | Log Analytics hot retention |
| `retention:lawArchiveDays` | `730` | Log Analytics archive retention |
| `retention:keyVaultSoftDeleteDays` | `90` | Key Vault soft-delete window (Azure minimum) |

Changing a retention value requires an ADR (`infra/README §12.2 rule 5`).

---

## 5. Verification

| Check | Test | Cadence |
|-------|------|---------|
| Cosmos `sessions` TTL applied per terminal-state transition | TASK-050 integration test (60-s TTL test fixture) | Per release |
| Cosmos `audit` TTL applied (365 d) | TASK-051 integration test | Per release |
| Audit archive job is idempotent + correctness | TASK-051 integration test | Per release |
| LAW retention matches policy | SECT-008 (`tests/specs/testing-matrix.md`) | T2 post-deploy |
| Audit retention divergence from session retention | SECT-009 + RES-005 | T2 + scheduled |
| Transcript retention applied | SECT-008 supplement | T2 scheduled |
| `audit-archive` Blob immutability policy active | Post-deploy assertion script (TASK-132) | Per deploy |
| GDPR erasure cascade end-to-end | New task (audit P2 §5.7) | Per release |

---

## 6. Pre-Public-Exposure Gate Items (Retention-Specific)

From [`docs/pre-public-gate.md`](./pre-public-gate.md):

- [ ] Each row in §1 has an enforcing test from §5.
- [ ] AppConfig values for retention match the env's policy.
- [ ] `audit-archive` Blob container exists with time-based immutability policy locked.
- [ ] LAW workspace retention is set per env (90 d hot in prod, 30 d in dev/qa).
- [ ] GDPR erasure flow is implemented and tested end-to-end.
