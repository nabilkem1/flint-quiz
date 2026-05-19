# ADR 002 — Single-Agent Architecture (Not Multi-Agent in v1)

- **Status**: Accepted
- **Date**: 2026-05-17
- **Last reviewed**: 2026-05-17

## Context

The system is a conversational multiple-choice quiz with a deterministic grader. The agent's job is to:

1. Greet the user, detect/confirm language, accept a topic and a question count.
2. Call tools that fetch questions, accept answers, return verdicts, and produce final results.
3. Serve both text (Playground) and voice (Realtime) channels.

Multi-agent designs (e.g., quizmaster + difficulty-adjuster + grader) are tempting but introduce coordination cost.

## Decision

**Use one agent in v1.** Multi-agent is not justified for a sequential MCQ flow.

## Rationale

- A sequential MCQ flow has no genuine separation of concerns to exploit.
- Multi-agent adds:
  - **Latency** — each handoff is a round-trip.
  - **Token cost** — context is replayed across agents or coordination protocols.
  - **Tracing complexity** — debugging cross-agent decisions is harder than reading one agent's tool-call trace.
- The deterministic grader is **Python in a tool**, not an "agent". This is the right boundary: grading correctness must not be LLM-mediated (see ADR 005).

## Alternatives Considered

| Approach                                       | Why not (yet)                                                                                                                                              |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Multi-agent (quizmaster + difficulty-adjuster) | Earns its place in v2 when **adaptive testing** introduces a genuine separation of concerns. Premature in v1.                                              |
| Multi-agent (quizmaster + grader)              | The grader is deterministic Python, not an LLM agent. Making it an agent would re-introduce non-determinism on a scored artifact users will dispute.       |
| Multi-agent (quizmaster + multilingual translator) | The Foundry models support `en`/`fr`/`es` natively; per-language phrasing blocks in a single system prompt are sufficient.                              |

## Consequences

### Positive

- Lower latency (critical for the voice path's ~300 ms p95 tool-execution budget — NFR-001).
- Simpler tracing: one agent, one tool-call loop, one telemetry surface.
- Lower token cost.

### Negative / Trade-offs

- When adaptive testing lands, we *will* need to introduce a second agent (or LangGraph / MAF Workflows). The single-agent architecture is intentionally a v1 choice, not a forever choice.

### Revisit When

- Adaptive/branching testing lands (v2): introduce a difficulty-adjuster agent or a LangGraph flow.
- Multi-step certification flow (proctored exams, retake logic, ID verification) lands: orchestration may be warranted.

## Links

- [specs/004-agent-behavior.md §1](../specs/004-agent-behavior.md)
- ADR [005-tool-boundary-prevents-answer-leakage](./005-tool-boundary-prevents-answer-leakage.md)
