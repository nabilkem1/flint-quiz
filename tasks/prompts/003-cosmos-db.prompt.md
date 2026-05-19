# DEV-STORY PROMPT — TASK-003 COSMOS DB (Durable Session State)

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 2 — Core Data Layer (Cosmos DB side)
**Current Task Pack**: 003-cosmos-db (containers, Pydantic models, repository, conditional writes, state machine, reproducible shuffle, retention, background sweeper)
**Scope**: Cosmos containers, typed contracts, repository layer with `ifMatch` etag conditional writes, session lifecycle state machine, reproducible-shuffle algorithm, TTL retention, audit two-stage archive, background sweeper for stranded/idle/expired transitions.

## TASK REFERENCES

- `tasks/003-cosmos-db.md`
  - TASK-040 — Database `flint-quiz` (autoscale 4000 RU/s)
  - TASK-041 — `sessions` container (`/userId`, TTL -1 default)
  - TASK-042 — `users` container (`/userId`, no TTL)
  - TASK-043 — `topics` container (`/topicId`)
  - TASK-044 — `audit` container (`/sessionId`)
  - TASK-045 — Pydantic models (`QuestionView`, `AnswerKey`, `SessionDoc`, `Answer`, `ResultsSummary`, `UserDoc`, `TopicDoc`, `AuditEvent`) with snake_case/camelCase bridge
  - TASK-046 — `cosmos_repository.py` read paths
  - TASK-047 — Conditional write with `ifMatch` etag (**non-negotiable idempotency**)
  - TASK-048 — Session state machine (`Active → Active|Paused|Expired|Completed → Scored`)
  - TASK-049 — Reproducible shuffle (SHA-256-seeded at `start_quiz`)
  - TASK-050 — TTL retention policy for `sessions`
  - TASK-051 — Audit retention (two-stage: hot Cosmos + immutable Blob archive, 7-year total)
  - TASK-191 — Background sweeper (Azure Function timer-triggered, 60s tick)
- Cross-pack dependencies:
  - `tasks/001-infrastructure.md` TASK-004, TASK-006, TASK-011
  - `tasks/002-ai-search.md` TASK-020

## SPEC REFERENCES

- `specs/003-data-contracts.md` — §4.3 (topics), §6 (Pydantic models, casing bridge), SEC-010
- `specs/008-api-contracts.md` — §0.4 (casing), §1.5.4, §1.5.6, §2 (model names), §2.3 (TopicDoc), §2.4 (AuditEvent), §3.3, §4.3 (state machine), §4.7 (timers + sweeper)
- `specs/005-security-model.md` — SEC-004, SEC-006, SEC-008, SEC-014
- `specs/004-agent-behavior.md` — §6 (normalisation), §10 (resumption)
- `specs/006-testing-strategy.md` — TEST-001, TEST-003, TEST-007, TEST-008, TEST-026, TEST-027

## ADR REFERENCES

- `adr/003-use-cosmos-db-for-session-state.md` — Cosmos is the authoritative session-state store
- `adr/005-tool-boundary-prevents-answer-leakage.md` — `QuestionView` vs `AnswerKey` typing discipline
- `adr/006-retention-policy.md` — two-stage audit retention + GDPR erasure boundary

## GOVERNANCE REFERENCES

- `docs/coding-standards.md` — Python async, Pydantic v2 patterns, type annotations
- `docs/ai-agent-development-guidelines.md` — durable vs ephemeral state, repository pattern discipline
- `docs/retention.md` — retention windows + justifications
- `infra/README.md` §3.1, §12.1 — UAMI rationale, retention surface

## OBJECTIVE

Implement the Cosmos data layer that:

1. Defines the four containers (`sessions`, `users`, `topics`, `audit`) with correct partition keys and retention stances.
2. Authors typed Pydantic v2 models (`SessionDoc`, `UserDoc`, `TopicDoc`, `AuditEvent`, `Answer`, `ResultsSummary`, `QuestionView`, `AnswerKey`) with snake_case ↔ camelCase aliasing and `_etag` mapping.
3. Builds the read-path repository (`get_session`, `get_user`, `list_topics`) — all partition-aware, all via Managed Identity.
4. Implements `append_answer_conditional` using Cosmos `ifMatch(_etag)` with bounded retry, no last-write-wins fallback, and **provable idempotency** keyed on `(session_id, question_id)`.
5. Encodes the session state machine in repository methods only (`pause_session`, `resume_session`, `expire_session`, `complete_session`); illegal transitions raise.
6. Implements the reproducible shuffle: `seed = sha256(session_id).hexdigest()`, `random.Random(int(seed[:16], 16))` derives the shuffled list; persist `seed` and `shuffledIds[]`.
7. Configures TTL retention: `sessions` get TTL on terminal-state transition; default 30 days from AppConfig.
8. Implements the two-stage audit retention: Cosmos hot 365 days, then idempotent archive to immutable Blob `audit-archive` for 7-year total.
9. Implements the background sweeper Azure Function (60-s timer) that performs stranded-release, per-quiz expiry, and inactivity-pause transitions using `ifMatch`-guarded writes.

## IMPLEMENTATION RULES

- **`ifMatch(_etag)` is non-negotiable** on every `submit_answer` write and every state-machine transition. On 412 PreconditionFailed, re-read; if the same `(question_id)` is already present in `answers[]`, return the existing `Session` (idempotent no-op). Otherwise retry once with the fresh etag. Max 1 retry; propagate beyond that.
- **`grading_event` emits ONLY on the successful conditional-write branch**, never on the idempotent no-op return. Hook is in 008-observability TASK-141; this pack must respect the boundary.
- **Partition-scoped reads only.** Cross-partition reads forbidden by code review. The sweeper feed query is the **only** cross-partition exception, justified by its maintenance role and the `_ts < now() - 60` predicate.
- **Pydantic v2 patterns**:
  - `model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)` for Cosmos-bound models.
  - Tool-I/O models use explicit snake_case field names (per `specs/008-api-contracts.md §0.4`).
  - `etag: str | None = Field(alias="_etag")` for conditional-write models.
  - Pin Pydantic ≥ 2.5 in `pyproject.toml` (handled in 004 but referenced here).
  - Strict round-trip property: `from_cosmos(to_cosmos(m)) == m` AND `from_tool(to_tool(m)) == m`.
- **Session state machine** is enforced **only** through repository methods. The state field is never mutated directly. Illegal transitions (e.g., `submit_answer` on `Expired`, `Scored → Active`) raise with a typed exception.
- **Server-side expiry**: compute `now - startedAt > timeLimitSeconds` against Cosmos `_ts`, not the model's stored timestamp; tolerate ±5 s drift.
- **Reproducible shuffle**: `seed` and `shuffledIds[]` persisted on the `SessionDoc`. If the RNG algorithm ever changes, version the field — do NOT silently change reproducibility for old sessions.
- **TTL retention**:
  - `sessions` TTL field is set **only** on transition to a terminal state (`Scored`/`Expired`/`Completed`). Active sessions older than `maxActiveAgeSeconds` get TTL on the next read.
  - Default `cosmosSessionsTtlDays` from AppConfig (30 days).
- **Audit two-stage retention** (per `adr/006-retention-policy.md`):
  - Cosmos `audit` TTL = 365 days (`retention:auditHotDays`).
  - Scheduled archive job writes rows with `_ts + ttl - 30 days <= now` to immutable Blob container `audit-archive` (time-based immutability, 7-year retention).
  - Archive job idempotent: re-archiving an already-archived row is a no-op (byte-equivalent check by hash).
- **Background sweeper** (TASK-191):
  - Azure Function with Timer trigger, 60-s tick, `infra/modules/foundry/sweeper.bicep`.
  - UAMI = `uami-agent-*`; RBAC = Cosmos Data Contributor scoped to `sessions` container only.
  - Feed query: `SELECT ... FROM c WHERE c.status IN ("Active","Paused") AND c._ts < now() - 60`.
  - Transition order (first match wins per row):
    1. Stranded release: `status="Active"` AND `currentIndex==0` AND `now - startedAt > voice:maxStrandedSeconds (300s)` → flip to `Expired`.
    2. Per-quiz expiry: `now - startedAt > timeLimitSeconds` → flip to `Expired`; auto-grade remaining as `unanswered`; emit one `grading_event` per remaining slot.
    3. Inactivity pause: `status="Active"` AND `now - questionStartedAt > pauseThresholdSeconds (600s)` AND `currentIndex > 0` → flip to `Paused`.
  - All writes use `ifMatch(_etag)`; 412 → log and skip (real user turn won the race).
  - Emit per-tick metrics: `sweeper.{stranded_released, expired_swept, paused_swept}`.
- **Identity discipline**: `cosmos_repository.py` constructs `CosmosClient` with `DefaultAzureCredential`. No keys or connection strings in source or env.

## OUTPUT FILES

Generate:

- `infra/modules/cosmos-database.bicep` — `flint-quiz` database + four containers with partition keys + TTL stance + indexing policies.
- `infra/modules/foundry/sweeper.bicep` — Azure Function (Timer trigger) for the background sweeper.
- `infra/modules/audit-archive.bicep` — immutable Blob container `audit-archive` (time-based immutability policy, 7-year retention).
- `src/data/models.py` — every Pydantic model: `SessionDoc`, `UserDoc`, `TopicDoc`, `AuditEvent`, `Answer`, `ResultsSummary`, `QuestionView`, `AnswerKey`, plus enums for session status, verdict, channel.
- `src/data/cosmos_repository.py` — read paths (`get_session`, `get_user`, `list_topics`), conditional write (`append_answer_conditional`), state-machine methods (`pause_session`, `resume_session`, `expire_session`, `complete_session`).
- `src/data/shuffle.py` — reproducible shuffle helper (`compute_seed(session_id) -> int`, `derive_shuffled_ids(seed, candidates) -> list[str]`).
- `src/data/audit_archive.py` — idempotent archive job (Cosmos → immutable Blob).
- `src/sweeper/main.py` (or `function_app.py`) — Azure Function entry point; queries the feed, applies transition rules with `ifMatch`.
- `tests/unit/test_models_roundtrip.py` — property tests on snake_case ↔ camelCase round-trip.
- `tests/unit/test_shuffle_determinism.py` — same `session_id` ⇒ same `shuffledIds[]`.
- `tests/integration/test_conditional_write.py` — etag concurrency + idempotent no-op.
- `tests/integration/test_state_machine.py` — allowed vs forbidden transitions (gates TEST-026).
- `tests/integration/test_audit_archive.py` — Cosmos row archives to immutable Blob byte-equivalent; re-running produces no duplicate.
- `tests/integration/test_sweeper.py` — stranded release, per-quiz expiry sweep, inactivity pause.

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **NFR-002 / SEC-006**: two concurrent `submit_answer` calls for the same `(session_id, question_id)` produce exactly one persisted answer and identical verdicts; one `grading_event` total; one `audit` row total.
- **NFR-003**: recomputing the shuffle from `seed` yields the persisted `shuffledIds[]`. Session is fully reconstructible from `(sessionId, seed, candidateIds[])`.
- **NFR-004 / FR-015**: per-question and per-quiz timers are server-side; client never decides time. Expired sessions auto-grade remaining as `unanswered`.
- **NFR-005**: Cosmos autoscale enabled at database level; partition keys match the spec.
- **NFR-007 / SEC-004**: every Cosmos client constructed with `DefaultAzureCredential`; CI grep finds zero `AccountKey=` patterns.
- **SEC-008 / SEC-014**: audit retention exceeds session retention; audit archive 7-year retention enforced via immutable Blob.
- **TEST-001**: containers created, partition keys correct, autoscale enabled, `disableLocalAuth: true` at account level (handled in 001).
- **TEST-007**: idempotency under concurrency (real Cosmos or emulator with `ifMatch`).
- **TEST-008**: resume after disconnect mid-quiz returns next unanswered question with score/answers preserved.
- **TEST-026**: every forbidden transition (`Scored→Active`, `Expired→Active`, `Completed→Active`) rejected; every allowed transition advances with `ifMatch`.
- **TEST-027**: sweeper flips silently-abandoned sessions to `Expired` on the next tick.
- **mypy --strict**: `src/data/models.py` clean.

## FORBIDDEN ACTIONS

- Do NOT fall back to last-write-wins on a 412 PreconditionFailed. The idempotent no-op (existing `question_id` in `answers[]`) or single retry are the only paths.
- Do NOT emit `grading_event` on the idempotent no-op return path (would double-count metrics — TEST-007 verifies).
- Do NOT mutate session state outside repository methods. The state field is private to the repository.
- Do NOT perform cross-partition reads or writes from tool-path code. The sweeper feed query is the ONE exception and runs only in the maintenance path.
- Do NOT include `correct_answer` in any persisted `SessionDoc` or `AuditEvent` field accessible to the LLM-safe tool path. `AuditEvent` does carry `expected` for dispute resolution, but it is RBAC-restricted (analyst/auditor only) and never crosses the App Insights surface.
- Do NOT add a JSON serializer to `AnswerKey`. A logging mistake must not leak the key.
- Do NOT use any Cosmos SDK feature that bypasses partition-key constraints (e.g., `enable_cross_partition_query=True`) outside the sweeper feed query.
- Do NOT compute `now - startedAt` from a client clock when Cosmos `_ts` is available. Server time is authoritative for expiry.
- Do NOT silently change the shuffle RNG algorithm. If the algorithm changes, version the field; old sessions remain reproducible.
- Do NOT set `sessions` TTL on Active sessions younger than `maxActiveAgeSeconds`. Premature reclamation breaks resume.
- Do NOT delete `audit` rows before archive. The archive job runs daily and archives 30 days before Cosmos delete.
- Do NOT implement agent code, tool surface, prompt composition, or grading logic. Those live in 004 and 005.
- Do NOT bypass the state machine on the sweeper path. The sweeper uses the same `ifMatch`-guarded transitions; a real user turn racing the sweeper wins via 412.
- Do NOT include user PII in the `agent.user_erased` event payload. Pseudonyms only (handled in 007 TASK-134; respect the boundary here).
