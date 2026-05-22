# ADR 003 — Use Cosmos DB for Durable Session State

- **Status**: Accepted
- **Date**: 2026-05-17
- **Last reviewed**: 2026-05-17

## Context

Quiz sessions are stateful and must survive:

- Disconnections mid-quiz (resume by `session_id` — FR-008).
- Channel switches mid-quiz (voice ↔ text on the same session — FR-009).
- Dispute resolution — exam systems get disputed; the system needs an authoritative record.

The Foundry-managed `AgentThread` provides ephemeral conversational state, but its schema is owned by Foundry and is a UX convenience, not a system of record.

## Decision

Use **Cosmos DB (NoSQL)** as the durable system of record for sessions, users, topics catalog, and audit log. Keep ephemeral conversational state in the Foundry-managed `AgentThread`.

This is a **two-tier state model**:

| Tier                       | Where                          | Lifetime  | Authority for                                                                  |
| -------------------------- | ------------------------------ | --------- | ------------------------------------------------------------------------------ |
| Ephemeral conversational   | Foundry-managed `AgentThread`  | Session   | Chat phrasing, last few turns                                                  |
| Durable session state      | Cosmos `sessions` container    | Permanent | Current question index, remaining IDs, answers, score, seed, language          |

## Rationale

- **Resumability**: server can rehydrate session state on reconnect or channel switch. Thread-only state is fragile here.
- **Auditability**: Cosmos is the system of record. Foundry threads are a UX convenience whose schema we don't own.
- **Token economy**: keeping `remaining_question_ids[]` in the LLM prompt is wasteful and grows unboundedly.
- **Idempotency**: Cosmos conditional writes (`ifMatch` + etag on `(session_id, question_id)`) prevent double-scoring on retries. **Non-negotiable** (NFR-002, SEC-006).
- **Partitioning**: `/userId` partition key gives near-linear scale (NFR-005).
- **Retention**: TTL on stale sessions handles cleanup without custom jobs.

## Alternatives Considered

| Approach                                                | Why not                                                                                                                                                                          |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM context only (rely on `AgentThread` for everything) | Loses resumability across disconnects and channel switches; not auditable; token-inefficient; no idempotency primitive.                                                          |
| Azure SQL                                               | Possible, but Cosmos is the Foundry-native managed-thread backing store; using it directly aligns with the platform and gives partitioning + TTL without operational overhead.   |
| Redis (sessions in cache)                               | Not durable enough for an exam system that gets disputed; Cosmos handles both durability and the conditional-write contract.                                                     |
| Foundry Agent Service managed Cosmos thread alone       | The managed thread is great for conversation state but doesn't expose the conditional-write idempotency contract we need on the grading hot path.                                |

## Consequences

### Positive

- Strong idempotency primitive (etag) for the grading hot path.
- Native partition-by-`/userId` scales horizontally.
- TTL handles retention cheaply.
- Separation of `audit` container from `sessions` lets retention policies differ.
- Cosmos is the Foundry-native data store for managed agent threads — using it directly keeps the stack coherent.

### Negative / Trade-offs

- Cosmos costs more per GB than blob, but the data volume is bounded by retention TTL and the workload is point-read / point-write friendly.
- Requires careful partition-key choice (`/userId` for sessions; `/sessionId` for audit) — already specified.

## Container Layout

- `sessions` — pk `/userId`, TTL, etag-driven idempotency.
- `users` — pk `/userId`, language preference.
- `topics` — small reference data, per-language labels and counts.
- `audit` — pk `/sessionId`, grading-correctness events, independent retention.

See [specs/003-data-contracts.md §4](../specs/003-data-contracts.md).

## Links

- [Cosmos DB integration with Foundry Agent Service](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/azure-agent-service)
- [specs/002-system-architecture.md §7](../specs/002-system-architecture.md)
- [specs/003-data-contracts.md](../specs/003-data-contracts.md)
