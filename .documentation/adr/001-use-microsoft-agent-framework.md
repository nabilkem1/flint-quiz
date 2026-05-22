# ADR 001 — Use Microsoft Agent Framework (Python) for the Agent

- **Status**: Accepted
- **Date**: 2026-05-17
- **Last reviewed**: 2026-05-17
- **Context window**: May 2026 Azure AI Foundry landscape

## Context

Microsoft's agent story consolidated in 2026:

- **Prompt Flow** is being retired (feature dev ended 2026-04-20; full retirement 2027-04-20).
- **Microsoft Agent Framework (MAF)** hit GA on 2026-04-03 as the strategic successor.
- **Foundry Agent Service** is GA, providing a managed runtime for MAF with Cosmos-backed threads and a Realtime (voice) API.
- **LangGraph** is GA and deploys *into* Foundry Agent Service alongside MAF; it pays off for graph/branching orchestration but is overkill for a sequential MCQ flow.

We need to pick the agent runtime that v1 is built on.

## Decision

Build the agent in **Microsoft Agent Framework (Python)**, deployed as a **Hosted Agent in Foundry Agent Service**.

## Alternatives Considered

| Approach                                          | Why not                                                                                                                                                                              |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Foundry Prompt Flow / Workflow UI                 | Being retired — feature freeze 2026-04, retirement 2027-04. Building here is technical debt on day one.                                                                              |
| LangGraph (alone)                                 | Pays off for graph/branching orchestration. Premature in v1 — adds orchestration complexity that the v1 sequential MCQ flow does not need.                                           |
| Hybrid MAF + LangGraph                            | MAF for the agent shell; LangGraph for adaptive/branching flows. Premature in v1; revisit when adaptive testing lands in v2.                                                         |
| Foundry Workflows (graph-based PF successor)      | Overkill; would re-introduce the orchestration layer we correctly excised.                                                                                                           |
| Durable Functions                                 | The quiz is short-lived and conversational, not a long-running workflow with checkpoints.                                                                                            |

## Consequences

### Positive

- Strategic Microsoft direction — investing in MAF aligns with where the ecosystem is going.
- First-class Foundry integration: identity, observability, Cosmos-backed memory, scaling, Realtime endpoints are managed.
- Built-in thread/state, tool-calling, telemetry — less custom plumbing.
- Same agent instance serves both text (Playground) and voice (Realtime) channels.

### Negative / Trade-offs

- MAF is young (GA 2026-04-03). Some patterns are still being established in the community.
- Requires Python; teams with C#/TypeScript preferences must accept Python-only for v1.

### Revisit When

- Adaptive testing / branching flows enter the roadmap → consider adding LangGraph or MAF Workflows alongside MAF (see ADR 002).
- Multi-step certification flows with checkpoints become a requirement.

## Links

- [Microsoft Agent Framework GA blog](https://devblogs.microsoft.com/foundry/microsoft-agent-framework-reaches-release-candidate/)
- [Prompt Flow retirement notice](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/prompt-flow-is-being-retired/4513587)
- [Foundry Agent Service overview](https://learn.microsoft.com/en-us/azure/foundry/agents/overview)
- [LangGraph + Foundry Agent Service](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-agents)
- Architecture: [specs/002-system-architecture.md](../specs/002-system-architecture.md)
