# DEV-STORY PROMPT — TASK-002 AI SEARCH (Multilingual Question Bank)

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 2 — Core Data Layer (AI Search side)
**Current Task Pack**: 002-ai-search (index schema, analyzers, seed loader, two-method search client — the answer-leakage boundary)
**Scope**: Define the AI Search index, per-language analyzers/synonyms, Blob authoring layout, seed loader, two-method search client (`get_question_view` LLM-safe vs `get_answer_key` server-only), per-language reindex pipeline with topic-counts reconciliation.

## TASK REFERENCES

- `tasks/002-ai-search.md`
  - TASK-020 — Index schema (`questions` index, one record per `(question_logical_id, language)`)
  - TASK-021 — Per-language analyzers (`en.microsoft`, `fr.microsoft`, `es.microsoft`)
  - TASK-022 — Synonyms maps per language
  - TASK-023 — Filterable / facetable field config
  - TASK-024 — Blob authoring layout (`questions/{lang}/<topic>/<logical_id>.json`)
  - TASK-025 — Initial seed content (≥30 questions × 3 languages = ≥90 docs)
  - TASK-026 — Seed loader `src/seed/seed_index.py`
  - TASK-027 — Two-method `question_search.py` client (security boundary)
  - TASK-028 — Per-language reindex pipeline + `topics.counts` reconciliation
  - TASK-029 — Index integration tests
- Cross-pack dependencies:
  - `tasks/001-infrastructure.md` TASK-005, TASK-006, TASK-011
  - `tasks/003-cosmos-db.md` TASK-043, TASK-045

## SPEC REFERENCES

- `specs/003-data-contracts.md` — §2.1 (questions index), §2.3 (Blob authoring layout)
- `specs/008-api-contracts.md` — §3.3 (two-method search client), §3.3.1 (`get_question_view`), §3.3.2 (`get_answer_key`), §0.4 (casing)
- `specs/005-security-model.md` — SEC-001, SEC-002, SEC-010
- `specs/006-testing-strategy.md` — TEST-002, TEST-004, TEST-005, TEST-006, TEST-011

## ADR REFERENCES

- `adr/004-use-ai-search-for-question-bank.md` — AI Search is the authoritative question store
- `adr/005-tool-boundary-prevents-answer-leakage.md` — **non-negotiable**: `correct_answer` never crosses the LLM boundary

## GOVERNANCE REFERENCES

- `docs/coding-standards.md` — Python style, naming, async patterns
- `docs/ai-agent-development-guidelines.md` — boundary discipline, defense-in-depth
- `docs/content-governance.md` — translation quality, per-language authoring
- `docs/llm-boundary.md` — what the LLM sees vs does not see

## OBJECTIVE

Implement the AI Search layer that:

1. Defines the `questions` index schema (Bicep deployment script, CI-owned via `uami-deploy-*`).
2. Configures per-language Microsoft analyzers and per-language synonyms maps.
3. Locks the Blob authoring directory shape and authors the initial seed set.
4. Provides a Python seed loader (`uami-indexer-*`) that walks Blob → Pydantic-validates → upserts index documents idempotently.
5. Exposes a two-method `question_search.py` client where `get_question_view` returns an LLM-safe projection (explicit `selected_fields` allowlist) and `get_answer_key` is server-only with no JSON serializer.
6. Implements per-language reindex with diff (added/updated/deleted) AND reconciles `topics.counts` in Cosmos against AI Search facet counts after every reindex.
7. Adds integration tests + AST checks that fail the build if `get_answer_key` is referenced anywhere except inside the `submit_answer` function body.

## IMPLEMENTATION RULES

- **Index lifecycle = Bicep / CI only.** The seed loader writes documents only. It must NOT have authority to create or delete the index. Assert at startup that the running identity does NOT have `Search Service Contributor`; abort on misconfiguration.
- **One record per `(question_logical_id, language)` pair** — IDs use `f"{logical_id}-{language}"`.
- **Per-language analyzers**: `en.microsoft`, `fr.microsoft`, `es.microsoft`. Validate the chosen approach (single `text` field with searchAnalyzer/indexAnalyzer pair vs language-suffixed fields) with a small benchmark before locking in.
- **Two-method client (TASK-027) is the single canonical security boundary**:
  - `get_question_view(question_id) -> QuestionView` selects `["id", "logical_id", "topic", "language", "text", "options", "difficulty"]` — an **explicit allowlist passed to AI Search**, so the result document literally does not contain `correct_answer`.
  - `get_answer_key(question_id) -> AnswerKey` selects only `["id", "correct_answer", "score_weight"]`, returns an `AnswerKey` dataclass with **no JSON serializer** (no `__json__`, no `model_dump_json`).
  - `search_topic(topic, language, difficulty) -> list[str]` returns logical IDs only.
  - The `get_answer_key` module-level docstring reads verbatim: `"Server-only. Never exposed via src/agent/tools.py."`
- **Seed loader idempotency**: running twice produces the same index state; `--confirm` flag required if proposed delete count > 10% of index.
- **Reindex pipeline** must, in this order:
  1. Compute added / updated / deleted set per language.
  2. Upsert and delete.
  3. Wait for AI Search indexer sync window (sleep 5 s + verify doc count matches expected).
  4. Run faceted `(topic, language)` cross-tab query.
  5. For each `topic_id` in Cosmos `topics`, replace `counts` map with the observed facet counts using `ifMatch(_etag)`.
  6. Emit `seed_loader.topic_mismatch` warning when index/topics disagree on the topic set; do NOT silently create or delete topic rows.
- **Identity discipline**: seed loader authenticates via `DefaultAzureCredential` and must resolve to `uami-indexer-*` in production. The reconciliation step uses a separate Cosmos `Data Contributor` scoped to `topics` only.
- **Snake_case for tool I/O, camelCase for Cosmos** — handled in `003-cosmos-db` Pydantic models but referenced here (the `QuestionView` exposed to tools is snake_case).
- **Synonyms maps namespaced per language** (`topic-synonyms-en`, `-fr`, `-es`) and attached to the `text` field per-analyzer profile.
- **Filterable fields**: `topic`, `language`, `difficulty`, `tags`, `category`, `logical_id`. Facetable: `topic`, `language`, `difficulty`.

## OUTPUT FILES

Generate:

- Bicep deployment script for index creation (extends `infra/modules/search.bicep` from 001-infrastructure):
  - `infra/scripts/create-questions-index.bicep` (or equivalent `deploymentScripts` resource)
  - `infra/scripts/questions-index-schema.json` (the index definition consumed by the deployment script)
- `infra/scripts/synonyms-en.json`, `synonyms-fr.json`, `synonyms-es.json`
- `src/data/question_search.py` — two-method client + `search_topic` helper
- `src/seed/seed_index.py` — Blob → AI Search seed loader (idempotent, identity-asserting)
- `src/seed/questions/README.md` — Blob authoring layout documentation
- `src/seed/questions/{en,fr,es}/<topic>/<logical_id>.json` — ≥30 logical questions × 3 languages = ≥90 files, across 3 topics (e.g., `azure-networking`, `azure-storage`, `azure-security`)
- `src/seed/reconcile_topics.py` (or integrated into `seed_index.py`) — facet query + Cosmos `topics.counts` reconciliation with `ifMatch`
- `tests/integration/test_question_search.py` — `language` filter, `get_question_view` has no `correct_answer`, `get_answer_key` returns the key, AST check on tool imports
- `tests/integration/test_reindex_reconciliation.py` — adds a question and asserts `topics.counts[topic][lang]` increments within one loader run

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **SEC-001 / SEC-002 / ADR-005**: `get_question_view` results contain no `correct_answer` field (unit test on the dict). `get_answer_key` is imported only inside the `submit_answer` function body (AST check that fails the build on violation).
- **NFR-006**: AI Search S1 with semantic search enabled (provisioned by 001-infrastructure).
- **NFR-010 / NFR-011**: one record per `(logical_id, language)`; per-language analyzers verified by smoke search (French query for "passerelle" matches only French index records).
- **FR-005 / FR-012**: filter by `language` returns only matching records; query without filter never returns cross-language results in tool-path code.
- **TEST-002**: 90 seed files validate against the Pydantic `Question` model; loader-twice produces identical index state.
- **TEST-006**: AST check rejects any function in `src/agent/tools.py` (other than `submit_answer` body) that references `get_answer_key`.
- **Idempotency**: re-running `seed_index.py` over an unchanged Blob set is a no-op (zero upserts, zero deletes).
- **Reconciliation**: after a reindex that changes `(topic, language)` counts, Cosmos `topics.counts` reflects the new facet counts within one loader run; index/topics mismatch emits a warning, never silent mutation.
- **Identity guard**: seed loader aborts with a clear error if its running identity has `Search Service Contributor`.

## FORBIDDEN ACTIONS

- Do NOT include `correct_answer` in the field list passed to AI Search by `get_question_view`. The allowlist is the first defense for SEC-001.
- Do NOT add a JSON serializer (`__json__`, `model_dump_json`, custom `__str__` that emits the value) on `AnswerKey`. A logging mistake must not leak the key.
- Do NOT import or reference `get_answer_key` anywhere outside the `submit_answer` function body. The AST lint will fail.
- Do NOT let `seed_index.py` create, delete, or alter the index schema. Control-plane lives in CI (`uami-deploy-*`); data-plane lives in the loader (`uami-indexer-*`).
- Do NOT silently create or delete `topics` catalog rows from the reconciliation path. Emit `seed_loader.topic_mismatch` and skip.
- Do NOT use `ORDER BY RAND()`-style queries against AI Search. Question shuffle is seeded in 003-cosmos-db TASK-049.
- Do NOT use API keys or connection strings. `DefaultAzureCredential` only.
- Do NOT skip the `--confirm` guard when proposed deletions exceed 10% of the index.
- Do NOT author one JSON file per logical_id containing all three translations. One file per `(logical_id, language)` pair — schema validator rejects merged files.
- Do NOT cache `correct_answer` values in process memory beyond the lifetime of a single `submit_answer` call.
- Do NOT implement grading logic, normalization, or tool surface in this pack — those live in 005-tools.
- Do NOT implement Cosmos repository methods (read/write paths) in this pack — those live in 003-cosmos-db.
