# Rollback Procedure — Flint Quiz

**Purpose**: How to roll back any production-affecting deploy of Flint Quiz. The agent, the question-bank index, and the per-language phrasing blocks each have a separate rollback path; Cosmos data is **not** rolled back.

**Owner**: Platform on-call.
**Audience**: on-call engineer with an active P0/P1 incident.
**Cross-references**: [`specs/007-operational-runbook.md §3`](../specs/007-operational-runbook.md), [`tasks/010-deployment.md` TASK-210](../tasks/010-deployment.md), [`infra/README.md §6.3`](../infra/README.md).

---

## 1. Principle — Rollback is a Forward Deploy

Per `infra/README §6.3 rule 5`: **rollback is a forward deploy of the prior tagged release commit**, not a portal patch and not a `git revert` followed by an out-of-band push. The audit trail of what is running in production is the Git tag history; deviating from that is a separate incident.

There is no portal-only fix path. There is no `azd up --rollback` flag. The procedure below is the only path.

---

## 2. Decision Tree

| Symptom | Likely root | Rollback path |
|---------|-------------|---------------|
| Agent prompt change introduced a refusal loop or wrong-language drift | Phrasing block or system-prompt regression | §3 Agent code rollback |
| `tests/test_no_answer_leakage.py` (TEST-006) red in prod canary | Tool layer regression | §3 Agent code rollback **immediately**; do not wait for the next release. P0. |
| Question-bank ambiguity flagged by per-language Foundry Evaluation | Authoring regression | §4 Index rollback |
| Cosmos schema drift (e.g., model serializer change) | Pydantic model regression | §3 Agent code rollback. **Cosmos data is not rolled back** (§5). |
| Bicep / infra deploy left a resource in a bad state | IaC regression | §6 Infra rollback (forward deploy of prior tag) |

---

## 3. Agent Code Rollback

The agent code is deployed via `azd deploy quiz-agent` to the Hosted Agent in the Foundry project. Rollback:

```bash
# 1. Identify the last-known-good release tag
git tag --list 'v*' --sort=-version:refname | head -5

# 2. Check out that tag in a fresh worktree
git worktree add /tmp/flint-rollback <prior-tag>
cd /tmp/flint-rollback

# 3. Select the target environment
azd env select prod

# 4. Forward-deploy the agent (do NOT run azd up; only azd deploy)
azd deploy quiz-agent
```

**Pre-checks before pressing Enter on step 4**:

- `git log <prior-tag>..HEAD` reveals what's being unwound.
- The prior tag's CI run shows green for TEST-006, TEST-007, TEST-019, TEST-025.
- The Pydantic models in the prior tag are compatible with the current Cosmos schema (a schema-breaking PR cannot be rolled back without a separate forward fix — see §5).

Post-deploy: run the post-deploy smoke matrix (`tasks/010 TASK-205`) and confirm green before declaring the incident resolved.

---

## 4. Question-Bank Index Rollback

The AI Search `questions` index is **rebuilt from Blob `authoring`** by the seed loader (`src/seed/seed_index.py`, `tasks/002 TASK-026`). Blob is the source of truth; AI Search is the runtime cache.

To roll back the index:

```bash
# 1. Identify the Blob version corresponding to the last-known-good authoring state.
#    Blob versioning is enabled (infra/README §1.1 row 9); list versions.
az storage blob list --account-name <storage> --container-name authoring \
  --query "[?versionId]"

# 2. Promote the prior version to current. (Either restore-version, or copy
#    the prior version to a new blob and re-point the seed loader.)
az storage blob copy start --source-blob ... --source-version-id ... \
  --destination-container authoring

# 3. Re-run the seed loader against the now-reverted authoring state.
python src/seed/seed_index.py

# 4. Verify per-language counts in the index match the prior state.
```

The seed loader is **idempotent** (`tasks/002 TASK-028`): adds, updates, and deletes converge on the authoring state. **Reindex from Blob is the rollback** — AI Search is never the source of truth.

**Per-language partial rollback** (e.g., revert French while keeping English current) requires editing the authoring tree to restore only the French files for that topic, then re-running the loader. The loader will diff and apply only the changed records.

---

## 5. Cosmos Data is NOT Rolled Back

`sessions`, `users`, `topics`, `audit` are systems of record. Rolling back data would defeat the audit trail and would corrupt in-flight sessions belonging to users who had nothing to do with the bad deploy.

If a deploy introduced **a Cosmos schema-breaking change** that is unsafe to leave running:

1. Forward-fix the schema bug in a new release (do not roll back).
2. If the bug created malformed rows, write a one-off migration task (Cosmos query + `replace_item` with `ifMatch`) that repairs them. Migration must preserve `_etag` semantics and emit `audit.data_migration` events.
3. The migration is treated as a P1 release on its own (PR, review, ADR if invasive).

Continuous backup PITR (30 days in prod, per `infra/README §9.3`) is **for disaster recovery**, not for rolling back a deploy. Restoring from PITR is a §7 last-resort path that requires Security + Platform approval.

---

## 6. Infrastructure (Bicep) Rollback

If a Bicep deploy left a resource in a bad state:

```bash
# 1. Check out the prior tag.
git checkout <prior-tag>

# 2. Re-deploy infra (forward deploy of the prior IaC).
azd provision --no-state
```

**`bicep what-if` is a required check** before the forward deploy. If the what-if shows destructive changes (e.g., a Cosmos account would be re-created, an AI Search index would be dropped), **stop**. A destructive rollback is a separate incident requiring Security + Platform approval.

The most likely destructive case is a Cosmos partition-key change — never resolved by rollback; resolved by forward migration.

---

## 7. Last-Resort: Cosmos PITR Restore

Only when (a) data corruption is irreversible from application code, (b) the impact is bounded to a known time window, and (c) Security + Platform approve.

1. File the incident; document the time window and impact scope.
2. Restore the Cosmos account to a paired-region copy at the chosen point-in-time (`infra/README §9.3`).
3. Re-point the agent at the restored account (Bicep parameter override + forward deploy).
4. Reconcile `audit` rows that were emitted during the restored window — they may be duplicates of what's in the live audit. Pseudonymize or quarantine them per Security guidance.

This path has never been exercised in v1 and is not part of regular DR drills. The DR drill (`infra/README §9.4`) tests **region failover**, which is structurally different from PITR restore.

---

## 8. Post-Rollback Validation

After any rollback (agent, index, or infra):

- [ ] `tests/test_no_answer_leakage.py` (TEST-006) green for `en`, `fr`, `es` against the restored stack.
- [ ] `tests/test_idempotency.py` (TEST-007) green.
- [ ] `tests/test_prompt_hash.py` (TEST-025) green — the prompt hash on a new session matches the expected value for the rolled-back code.
- [ ] Post-deploy smoke matrix (TEST-003/004/005/010) green.
- [ ] Cost dashboard does not show an anomaly window.
- [ ] Audit log shows the deploy/rollback events for the change window.

Once all of the above are green, the incident may be closed.

---

## 9. Dry-Run Cadence

Per `tasks/010 TASK-210`: a **dry-run rollback** of the agent code path runs in `qa` at least quarterly. The dry run executes §3 against a fresh `qa` deploy and asserts post-deploy smoke. A failing dry run blocks the next production deploy until remediated (mirrors the DR-drill discipline).

---

## 10. Anti-Patterns

| Anti-pattern | Why it's wrong |
|--------------|----------------|
| `git revert <bad-commit>` and force-push | Loses audit history; the prior tag is the rollback target, not a synthesized commit. |
| Edit a phrasing block file in the portal | All resources are Bicep-managed; portal changes trigger drift detection (nightly `bicep what-if`). |
| Roll back Cosmos data | See §5. |
| Skip the `bicep what-if` step on a forward deploy of a prior tag | Bicep deploys can be destructive; the what-if is the safety net. |
| Force-push a `public-ready`-tagged release | Pre-public gate (`docs/pre-public-gate.md`) is not bypassable; force-push to that tag namespace is rejected at the platform level. |
