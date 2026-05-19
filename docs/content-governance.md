# Content Governance — Authoring, Review, Publish, Rollback

**Version**: v1.0
**Last reviewed**: 2026-05-17
**Owner**: Content team + Platform
**Status**: Accepted

**Purpose**: How question-bank content moves from author keyboard to live index, who reviews it, how per-language quality is gated, and how to roll a specific language back without disturbing others.

**Cross-references**: [`adr/004-use-ai-search-for-question-bank.md`](../adr/004-use-ai-search-for-question-bank.md), [`adr/006-retention-policy.md`](../adr/006-retention-policy.md), [`tasks/002-ai-search.md`](../tasks/002-ai-search.md), [`tasks/009-testing.md` TASK-167](../tasks/009-testing.md) (Foundry Evaluation per language), [`docs/rollback.md`](./rollback.md).

---

## 1. Roles

| Role | Entra group | What they do |
|------|-------------|--------------|
| **Subject-matter author** | `group:flint-content-author` | Writes new questions in JSON, one record per `(logical_id, language)` pair (NFR-011). |
| **Per-language reviewer** | `group:flint-content-review-{en,fr,es}` | Verifies linguistic correctness, idiomatic phrasing, cultural appropriateness in the target language. Sign-off is per-language. |
| **Subject-matter approver** | `group:flint-content-approve` | Final approval for technical correctness of the answer key. |
| **Content lead** | `group:flint-content-lead` | Owns the topic taxonomy + label translations in the `topics` Cosmos container. Approves new topics. |
| **Release manager** | `group:flint-release` | Runs the reindex + evaluation gate before publish. |

A single human can hold multiple roles; in practice, the **per-language reviewer** and the **subject-matter approver** must be distinct people for the same question (compliance separation of duties).

---

## 2. Authoring Workflow

### 2.1 Tooling (v1)

Authors edit JSON files directly under `src/seed/questions/{en,fr,es}/<topic>/<logical_id>.json`. The shape is locked by [`specs/003-data-contracts.md §2.1`](../specs/003-data-contracts.md) (and `008-api §3` for the index schema).

In v1 there is **no dedicated authoring UI**. The trade-off is:

- **Pro**: zero vendor lock-in; Git diff is the audit trail; no separate auth surface to secure.
- **Con**: authors edit JSON by hand. Mitigation: a JSON-schema-aware editor (VS Code with the `Question` schema pinned in `.vscode/settings.json`) + a `make validate` step that runs Pydantic against every authored file before the PR can merge.

A dedicated UI is a v2 enhancement (`specs/001-product-requirements.md §6` row "AI-generated questions"-adjacent).

### 2.2 Writing a New Question

1. Pick a `logical_id` — kebab-case, prefixed by topic (e.g., `az-net-0042`). Must not exist in any language file under the same topic.
2. Author all three language records: `en/<topic>/<logical_id>.json`, `fr/<topic>/<logical_id>.json`, `es/<topic>/<logical_id>.json`. **All three** must be written before merge — partial-language additions are rejected (`tasks/002 TASK-024` validation).
3. Fields required per record:
   - `id`, `logical_id`, `topic`, `language`, `text`, `options[]`, `correct_answer[]`, `difficulty`, `tags[]`, `category`, `explanation` (optional but encouraged per language), `score_weight`.
4. Run `make validate` locally. The validator runs:
   - JSON-schema validation against `Question` (and `QuestionWithAnswer` for the answer-key path).
   - Per-language `<topic>/<logical_id>.json` triple-presence check.
   - Forbidden-token scan: no answer-key strings from existing questions accidentally leaking into prompt-block files (TEST-018 supplement).

### 2.3 Editing an Existing Question

- Editing a **non-answer-key field** (text phrasing, option text, tags, explanation) in a single language is allowed and does not require the other-language records to change.
- Editing the **`correct_answer` field** requires synchronous editing of all three language records — drift between languages on the answer key is a P0 in the content workflow.
- **`logical_id` is immutable.** To replace a question, deprecate the old one (set `enabled: false` in a follow-up step — out of v1 scope) and author a new `logical_id`.

---

## 3. Review Workflow

### 3.1 Per-Language Review

Every authored or edited question opens a PR that the per-language reviewer for the affected language(s) must approve. CI enforces this via CODEOWNERS:

```
# CODEOWNERS — required reviewers per language
src/seed/questions/en/  @flint-content-review-en
src/seed/questions/fr/  @flint-content-review-fr
src/seed/questions/es/  @flint-content-review-es
```

Each reviewer checks:

- Linguistic correctness (grammar, idiom, accent).
- Cultural appropriateness (no region-specific assumptions that break for other speakers of the language).
- TTS-friendliness on a quick voice playthrough for that language (`docs/playground.md §3`).

### 3.2 Subject-Matter Approval

For the answer key, a separate approver from `group:flint-content-approve` signs off. This approver is **not** the author (separation of duties).

### 3.3 What Reviewers Look For (Per-Language)

| Concern | Example |
|---------|---------|
| Ambiguity in the new language | A French translation of "select all that apply" rendered as "choisir une option" (singular) — wrong shape. |
| Cultural shift in difficulty | An English question about US-only RFC examples translated literally — Spanish/French speakers may not recognise the example. Reviewer flags for rephrasing. |
| Loanword vs translation | "VPN gateway" stays as "VPN gateway" in French (loanword); "réseau" stays translated. Reviewer enforces the convention. |
| TTS pronunciation hazards | Raw URLs in question text → reviewer flags (NFR-014; TASK-087 should strip but authoring discipline catches first). |
| Per-language explanation drift | Explanation in French names different concepts than English. Reviewer flags. |

---

## 4. Per-Language Quality Gate (Foundry Evaluation)

Per `NFR-010` and [`tasks/009-testing.md` TASK-167](../tasks/009-testing.md):

- On every reindex (`make reindex` or CI), per-language Foundry Evaluations run against the staged index.
- Evaluators score: **difficulty drift**, **ambiguity**, **answer-key correctness over time**.
- A per-language regression outside tolerance blocks the publish for **that language only** — other languages may still publish, with the affected language's previous version remaining in the live index.
- Tolerance values: documented in `infra/main.parameters.<env>.json` (`evals:driftTolerance`, `evals:ambiguityFloor`).

---

## 5. Publish Workflow

Once all reviewers + approver have signed off, and the PR has merged:

1. **Release manager** (or scheduled CI) runs `python src/seed/seed_index.py` (`tasks/002 TASK-026`) against staging.
2. Loader diffs and applies (`tasks/002 TASK-028`); reconciles `topics.counts` from AI Search facets.
3. Foundry Evaluation runs per language; if green for all languages, the staging index is **aliased** to the production index name (`tasks/002 TASK-029` blue/green).
4. Post-publish smoke (`tasks/010 TASK-205`): TEST-003/004/005 against the new index.
5. The publish event is recorded in `audit` (separate from grading audits) — partition key `system:publish`.

**A failed evaluation in language X**:

- Production index alias does not flip for language X (its previous version remains live).
- The reindex completes for the other languages.
- An `agent.coverage_gap` event will fire on the next `start_quiz` for that language if coverage was lost (per `tasks/008 TASK-149`); content team is paged.

---

## 6. Per-Language Rollback

The most common content-incident pattern: "We just shipped a French translation of az-net-0042 that's actually wrong."

### 6.1 Procedure

1. Revert the offending JSON file(s) in `src/seed/questions/fr/<topic>/<logical_id>.json` to the prior version (Git history).
2. Open a hot-fix PR with the revert.
3. Per-language reviewer approves (same CODEOWNERS rule).
4. Merge → CI triggers `seed_index.py` automatically.
5. Loader diffs the change; updates only the affected `(logical_id, language)` documents.
6. Foundry Evaluation re-runs for French; passes (now that the bad question is gone).
7. Production index reflects the revert within ~5 minutes of merge.

**English and Spanish records for the same `logical_id` are not touched** — that's the per-record-per-language model in action (NFR-011).

### 6.2 What This Avoids

- No need to reindex from a prior Blob snapshot (the v1 default rollback per `docs/rollback.md §4`) — that would temporarily disturb English and Spanish records too.
- No outage in the other languages while the fix lands.

### 6.3 When To Use Full Rollback Instead

If the regression is **structural** (e.g., a per-language analyzer change broke French queries), use `docs/rollback.md §4` (full reindex from Blob at the prior tag). Per-record edits don't fix infrastructure.

---

## 7. Topic-Catalog Changes

Topics live in the Cosmos `topics` container. Adding or renaming a topic is **not** the seed loader's job — it's an authoring decision owned by `group:flint-content-lead`.

To add a new topic:

1. Content lead opens a PR adding the topic to a seed/migration script that writes the `TopicDoc` row.
2. Authors immediately add at least one question in every supported language under that topic.
3. CI runs the seed loader against staging.
4. Per-language Foundry Evaluation runs (NFR-010); the new topic's questions must score within tolerance per language.
5. Production reindex follows the standard publish workflow.

To rename a topic:

- **Don't.** `logical_id` and `topicId` are immutable. Create a new topic; deprecate the old one with `enabled: false` in a follow-up release (v2 enhancement).

---

## 8. Author Tooling Roadmap (v2)

| Tool | When |
|------|------|
| Dedicated authoring UI (web form, schema-aware) | v2 — paired with AI-generated questions feature |
| Per-question history / blame view | v2 — Git already provides this; UI is a convenience |
| AI-assisted translation suggestions | v2 — requires per-language eval pipeline as the safety net |
| Per-language difficulty histogram tooling | v2 — feeds adaptive testing |

In v1, the discipline is: **JSON + PR review + per-language Foundry Evaluation**. That trio is sufficient to ship and defend against the per-language drift risk (Risk 3 in [`specs/001-product-requirements.md §7`](../specs/001-product-requirements.md)).

---

## 9. Compliance & Audit

- Every content change is in Git history. The Git tag at publish time is the auditable artifact.
- The `audit` Cosmos container records grading events; the **content publish event** is a separate row partition (`system:publish`) for traceability.
- A user dispute about a specific question's correctness triages by:
  1. `audit.questionId` → identify the question and the language.
  2. Git history of `src/seed/questions/{lang}/<topic>/<logical_id>.json` → see who authored/approved that version.
  3. CODEOWNERS reviewer signoff at publish time → see who validated the language.
  4. Foundry Evaluation report at publish time → see whether the eval flagged it (false negative).

This chain is the value of the per-record-per-language model: a dispute scopes to one record + one language + one publish event.
