# 003 — Data Contracts

- **Version**: v1.0 (superseded by `008-api-contracts.md` for wire-level details — this doc is the summary)
- **Last reviewed**: 2026-05-17
- **Owner**: Platform
- **Status**: Accepted

This document defines schemas and contracts for: tool inputs/outputs, the question bank index, Cosmos containers, and configuration.

## 1. Data Storage Map

| Data                                     | Store                                                            | Why                                                                                                                                                            |
| ---------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Question bank                            | **Azure AI Search**                                              | Faceted filters (topic, difficulty, tags, language), semantic topic matching for free, decouples authoring from runtime. Multilingual analyzers per language. |
| Authoring source of truth                | **Blob Storage** (JSON/YAML files, per-language folders)         | Cheap, versionable, source-of-truth before indexing                                                                                                            |
| Session state, user answers, results     | **Cosmos DB NoSQL** (`sessions` container)                       | Write-heavy, partition by `/userId`, TTL on stale sessions, conditional writes for idempotency                                                                 |
| Users / identity / language preference   | **Cosmos DB** (`users` container)                                | Co-located with sessions                                                                                                                                       |
| Topic catalog (per language)             | **Cosmos DB** (`topics` container)                               | Small reference data + per-language label map + counts                                                                                                         |
| Audit log                                | **Cosmos DB** (`audit` container, pk `/sessionId`)               | Grading-correctness events; separate from session for retention policy                                                                                         |
| Secrets                                  | **Key Vault**                                                    | Managed Identity, no keys in code                                                                                                                              |
| Config                                   | **App Configuration**                                            | Model deployment name, search endpoint, supported languages, feature flags                                                                                     |

## 2. Question Bank (Azure AI Search)

### 2.1 Record Shape

**One record per `(question_logical_id, language)` pair.** Cleaner facets, cleaner per-language analytics, allows different option ordering or culturally-adjusted phrasing per language.

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

### 2.2 Index Configuration (Multilingual)

- `text` field uses the appropriate language analyzer per record (`fr.microsoft`, `es.microsoft`, `en.microsoft`).
- `language` field is **filterable**; every query filters by it.
- Synonyms maps per language for topic aliases.
- `correct_answer` is stored in the index but **must not be returned via tool surfaces that pass through the LLM** — only the server-side `submit_answer` path may read it. (See SEC-001/SEC-002.)

### 2.3 Authoring Source

JSON/YAML files in Blob Storage, organized per-language:

```
src/seed/questions/
  en/*.json
  fr/*.json
  es/*.json
```

A one-shot loader (`src/seed/seed_index.py`) reindexes Blob → AI Search with per-language analyzers.

## 3. Tool Contracts

All tools live in `src/agent/tools.py`. Inputs/outputs are Pydantic-modeled (see `src/data/models.py`).

| Tool                                                                        | Purpose                                                | Security note                                                                                  |
| --------------------------------------------------------------------------- | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `list_topics(language)`                                                     | Available topics, localized labels                     | None                                                                                           |
| `set_language(user_id, lang)`                                               | Persist user's preferred language                      | Validate against ISO 639-1 allowlist (SEC-010)                                                 |
| `start_quiz(user_id, topic, n, language, difficulty?)`                      | Create session, seed shuffle, return Q1                | Returns text + options ONLY (no answer keys). SEC-001.                                         |
| `submit_answer(session_id, question_id, answer)`                            | Grade deterministically, persist, return next Q        | `correct_answer` fetched server-side; never returned upstream. Idempotent via etag. SEC-002/SEC-006. |
| `get_results(session_id)`                                                   | Final score + breakdown in user's language             | Read-only                                                                                      |

### 3.1 Tool Return Contract (Security Boundary)

- Tools that fetch questions return `{question_id, text, options[], metadata}` — **never** `correct_answer`. The agent's LLM context must never see the answer key. (SEC-001)
- Only `submit_answer` reads `correct_answer`, and only server-side. The verdict goes back; the key does not. (SEC-002)

### 3.2 TTS-Friendly Return Shape

Tool return strings must be:

- Sentence-length, no markdown, no code blocks.
- Options spoken as: *"Option A: ... Option B: ..."* — never as a list rendered for screen.
- Numerals expanded ("ten questions" not "10 questions") for cleaner TTS.
- Phonetic-safe: avoid raw URLs; expand acronyms on first mention.

(See NFR-014.)

## 4. Cosmos DB Containers

### 4.1 `sessions`

- **Partition key**: `/userId`
- **TTL**: enabled; applied per retention policy to completed/stale sessions.
- **Concurrency**: conditional writes via `ifMatch` etag keyed on `(session_id, question_id)`. (NFR-002, SEC-006)

Conceptual fields (authoritative wire-level schema in [`008-api-contracts.md §2.1`](./008-api-contracts.md)):

```json
{
  "id": "<sessionId>",
  "userId": "<userId>",
  "topic": "azure-networking",
  "language": "fr",
  "requestedLanguage": "fr",
  "seed": "<sha256(session_id)[:16]>",
  "shuffledIds": ["az-net-0042-fr", "az-net-0010-fr", "..."],
  "currentIndex": 3,
  "answers": [
    {"question_id": "az-net-0042-fr", "received_raw": "la deuxième", "received_normalized": "B", "verdict": "correct", "score_delta": 1.0, "answered_at": "...", "channel": "voice", "latency_ms": 142}
  ],
  "score": 2.0,
  "maxScore": 5.0,
  "passThresholdPct": 60.0,
  "status": "Active | Paused | Expired | Completed | Scored",
  "startedAt": "...",
  "questionStartedAt": "...",
  "timeLimitSeconds": 600,
  "perQuestionLimitSeconds": 60,
  "channel": "voice",
  "_etag": "..."
}
```

`channel` records the most-recent channel used (text or voice). `requestedLanguage` differs from `language` only when a coverage-fallback consent flow has switched the resolved language (see [GOV-025](./009-agent-governance.md) and TASK-189).

### 4.2 `users`

- **Partition key**: `/userId`
- Stores per-user preferences including `language` (ISO 639-1, validated allowlist).

```json
{
  "id": "<userId>",
  "userId": "<userId>",
  "language": "fr",
  "createdAt": "...",
  "updatedAt": "..."
}
```

### 4.3 `topics`

- **Partition key**: chosen per scale needs (e.g., `/topicId` or single logical partition for small catalogs).
- Per-language label map + per-language question counts. Small, slow-changing — candidate for cache via App Configuration polling reload.

```json
{
  "id": "azure-networking",
  "labels": {"en": "Azure Networking", "fr": "Réseau Azure", "es": "Redes de Azure"},
  "counts": {"en": 120, "fr": 85, "es": 60}
}
```

### 4.4 `audit`

- **Partition key**: `/sessionId`
- Records grading-correctness events for dispute resolution and analytics. Separate from `sessions` so retention policy can differ.

Conceptual fields per event (see also NFR-009):

```json
{
  "sessionId": "...",
  "questionId": "...",
  "language": "fr",
  "channel": "voice | text",
  "expected": ["B"],
  "received": "B",
  "verdict": "correct | incorrect | partial | unanswered",
  "latencyMs": 142,
  "timestamp": "..."
}
```

## 5. Identity & Configuration

| Concern   | Store               | Notes                                                                                                       |
| --------- | ------------------- | ----------------------------------------------------------------------------------------------------------- |
| Secrets   | Key Vault           | Managed Identity; no keys in code. (SEC-004)                                                                |
| Config    | App Configuration   | Model deployment name, AI Search endpoint, supported languages, feature flags. Polling reload for `topics`. |

## 6. Pydantic Models

Defined in `src/data/models.py`. Tool I/O uses **snake_case** (per [`008-api-contracts.md §0.4`](./008-api-contracts.md)); Cosmos documents use **camelCase**; Pydantic models bridge the two via field aliases.

- `QuestionView` — LLM-safe projection of an AI Search record. **Has no `correct_answer` field at all** (explicit allowlist projection from the index, not field stripping). Returned by `get_question_view`. See [`008-api §1.5.4`](./008-api-contracts.md).
- `AnswerKey` — server-only dataclass returned by `get_answer_key`. No JSON serializer; never crosses the LLM boundary.
- `SessionDoc` — sessions container row. Schema in [`008-api §2.1`](./008-api-contracts.md).
- `Answer` — single answer event embedded in `SessionDoc.answers[]`.
- `ResultsSummary` — final scoring shape returned by `get_results` and embedded in `submit_answer.results` on quiz completion. Schema in [`008-api §1.7`](./008-api-contracts.md).
- `UserDoc` — users container row.
- `TopicDoc` — topics container row.
- `AuditEvent` — audit container row.

## 7. Language Codes

Validated against an ISO 639-1 allowlist (SEC-010). Initial supported set: `en`, `fr`, `es`. The allowlist itself lives in App Configuration so new languages can be enabled at runtime once content is authored and indexed.
