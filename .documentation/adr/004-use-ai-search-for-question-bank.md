# ADR 004 — Use Azure AI Search for the Multilingual Question Bank

- **Status**: Accepted
- **Date**: 2026-05-17
- **Last reviewed**: 2026-05-17

## Context

The question bank must support:

- Faceted filtering by topic, difficulty, tags, and **language** (every query filters by language — NFR-011).
- Multilingual analyzers per record (`en.microsoft`, `fr.microsoft`, `es.microsoft`).
- Read-heavy access on the voice hot path (NFR-001: ~300 ms p95).
- Decoupling authoring (versionable source files) from runtime (indexed for fast filtered reads).
- Per-language quality evaluation (NFR-010) for drift, ambiguity, and answer-key correctness.

## Decision

Use **Azure AI Search** for the runtime question bank. Use **Blob Storage** as the authoring source of truth (JSON/YAML, per-language folders), reindexed into AI Search by a one-shot loader.

## Rationale

- **Faceted filters** (topic, difficulty, tags, language) are first-class in AI Search.
- **Semantic topic matching** comes for free, useful for topic-aliasing.
- **Per-language analyzers** are configurable per record; this fits the "one record per `(logical_id, language)` pair" data model (NFR-011).
- **Synonyms maps per language** for topic aliases.
- **Decouples authoring from runtime**: authors edit JSON/YAML in Blob; the reindex pipeline pushes to AI Search. Source-of-truth lives outside the runtime store.

## Record Shape

One record per `(question_logical_id, language)` pair. See [specs/003-data-contracts.md §2](../specs/003-data-contracts.md).

```json
{
  "id": "az-net-0042-fr",
  "logical_id": "az-net-0042",
  "topic": "azure-networking",
  "language": "fr",
  "text": "Quel service Azure ...",
  "options": [{"key": "A", "text": "..."}, ...],
  "correct_answer": ["B"],
  "difficulty": "medium",
  "tags": ["vpn", "passerelle"],
  "category": "networking",
  "explanation": "...",
  "score_weight": 1.0
}
```

## Alternatives Considered

| Approach                                              | Why not                                                                                                                                                                                       |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cosmos DB for both questions and sessions             | Cosmos can hold the data, but it doesn't give per-language analyzers, faceted filters, semantic matching, or synonyms maps without significant custom code.                                   |
| Postgres + pg_trgm / full-text search                 | Workable but adds an operational dependency (DB ops) and underperforms AI Search's per-language analyzer story for a multilingual bank.                                                       |
| Static JSON in Blob, no index                         | Cheap, but every filtered query scans the world. Doesn't scale beyond a small bank and gives nothing for multilingual drift evaluation.                                                       |
| One record per `logical_id` with translations nested  | Looks tidier but breaks per-language facets, per-language analytics, and makes per-language Foundry Evaluations harder. Rejected; see NFR-011.                                                |

## Consequences

### Positive

- Filtered queries by `(topic, language, difficulty)` are fast and cheap.
- Per-language analyzers improve match quality without code changes.
- Adding a language = author + reindex (no code change). FR future-friendly.
- Per-language Foundry Evaluations against the index are straightforward.
- Source-of-truth in Blob is cheap and versionable.

### Negative / Trade-offs

- Two stores to manage (Blob authoring + AI Search runtime) — but the reindex is a simple one-shot loader.
- AI Search index updates are eventual relative to Blob — acceptable for an exam system where the bank is intentionally curated.

## Security Note

`correct_answer` is stored in the index but **must not be returned via tool surfaces that pass through the LLM**. Only the server-side `submit_answer` path may read it. (See ADR 005 and SEC-001/SEC-002.)

The "question search" client (`src/data/question_search.py`) exposes two distinct methods:

- A public method that returns text + options only (used by `start_quiz` / "fetch next question").
- A server-only method that fetches `correct_answer` (called inside `submit_answer` and never bubbled back to the agent).

## Synonyms-Map Versioning and Ownership

Per-language synonym maps (e.g., FR: "réseau" ↔ "réseaux") affect query recall and are surface area for content drift. Discipline:

- **Owner**: content team per language (CODEOWNERS entry: `/infra/search/synonyms/fr.yaml @content-fr`, etc.).
- **Storage**: source-of-truth in `infra/search/synonyms/{lang}.yaml`, applied at index time by the seed loader (`src/seed/seed_index.py`).
- **Versioning**: synonyms updates ride the index-version flip (`questions-vN+1`) — never applied in-place to a live index. This keeps the alias-flip rollback story uniform across schema and synonyms changes.
- **Partial-update window**: during the index rebuild, the old `questions-vN` continues to serve from its synonyms snapshot. Users see no synonym-related drift during the flip.
- **Test**: per-language synonyms tests under `tests/test_synonyms_{lang}.py` assert that a known recall set returns the expected superset on canonical → synonym substitution.

## Scaling

- Start at S1.
- Scale up if the bank grows beyond ~100k items (NFR-006).
- Read-heavy + small workload fits comfortably.

## Links

- [specs/003-data-contracts.md](../specs/003-data-contracts.md)
- [specs/002-system-architecture.md §6.5](../specs/002-system-architecture.md)
- ADR [005-tool-boundary-prevents-answer-leakage](./005-tool-boundary-prevents-answer-leakage.md)
