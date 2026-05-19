# Dev-Story Prompts

One spec-driven implementation prompt per task pack in `tasks/`. Each prompt follows the same enterprise structure:

1. **IMPLEMENTATION CONTEXT** — phase + scope
2. **TASK REFERENCES** — links to `tasks/NNN-*.md`
3. **SPEC REFERENCES** — links to `specs/NNN-*.md`
4. **ADR REFERENCES** — links to `adr/NNN-*.md`
5. **GOVERNANCE REFERENCES** — links to `docs/*.md`
6. **OBJECTIVE** — what this pack produces
7. **IMPLEMENTATION RULES** — discipline the implementer must follow
8. **OUTPUT FILES** — exhaustive list of files generated
9. **VALIDATION REQUIREMENTS** — testable evidence of correctness
10. **FORBIDDEN ACTIONS** — what does NOT belong in this pack

## Index

| Phase | Prompt | Task Pack |
|-------|--------|-----------|
| 1 — Infrastructure Foundation | [001-infrastructure.prompt.md](./001-infrastructure.prompt.md) | `tasks/001-infrastructure.md` |
| 2 — Core Data Layer (AI Search) | [002-ai-search.prompt.md](./002-ai-search.prompt.md) | `tasks/002-ai-search.md` |
| 2 — Core Data Layer (Cosmos DB) | [003-cosmos-db.prompt.md](./003-cosmos-db.prompt.md) | `tasks/003-cosmos-db.md` |
| 3 — Agent Layer | [004-agent-framework.prompt.md](./004-agent-framework.prompt.md) | `tasks/004-agent-framework.md` |
| 3 — Tool Layer (**CRITICAL**) | [005-tools.prompt.md](./005-tools.prompt.md) | `tasks/005-tools.md` |
| 4 — Voice Layer | [006-voice-realtime.prompt.md](./006-voice-realtime.prompt.md) | `tasks/006-voice-realtime.md` |
| 5 — Security Hardening | [007-security.prompt.md](./007-security.prompt.md) | `tasks/007-security.md` |
| 6 — Observability | [008-observability.prompt.md](./008-observability.prompt.md) | `tasks/008-observability.md` |
| 7 — Testing & Evaluation | [009-testing.prompt.md](./009-testing.prompt.md) | `tasks/009-testing.md` |
| 8 — Deployment & Operational Polish | [010-deployment.prompt.md](./010-deployment.prompt.md) | `tasks/010-deployment.md` |

## How to run

For each prompt, paste it verbatim into a fresh Claude session with this repo loaded. The prompt is self-contained: it states scope, references, rules, outputs, validation, and forbidden actions. Claude implements the pack; CI verifies against the VALIDATION REQUIREMENTS before the next prompt.

Run in dependency order — see `docs/implementation-roadmap.md` for the Mermaid dependency graph and project-phase rollup.

## Why this structure

Every code-generation step is anchored to a requirement, an architecture decision, a governance rule, and a validation gate. That chain prevents:

- AI drift (the model invents file paths or schemas that don't match the specs)
- Architectural inconsistency (one pack bleeds into another's responsibilities)
- Undocumented shortcuts (validation requirements demand evidence)
- Security regressions (TEST-006 leak, TEST-007 idempotency, TEST-028 GDPR gate every merge/release)

The FORBIDDEN ACTIONS section is as important as the OBJECTIVE: it scopes the implementer out of work that belongs elsewhere, preventing duplicated or conflicting code across packs.
