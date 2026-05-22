# DEV-STORY PROMPT — TASK-010 DEPLOYMENT & OPERATIONAL POLISH

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 8 — Deployment & Operational Polish
**Current Task Pack**: 010-deployment (azd up validation, seed reindex, smoke matrix, Playground configuration, Realtime smoke, per-release quality gate, pre-public exposure gate, rollback procedure, cost monitoring, phase-progression management)
**Scope**: Make the system deployable end-to-end via `azd up` from a clean subscription, seed content, run smoke tests, and codify operational gates (pre-deploy / post-deploy / per-release / pre-public). Includes rollback, cost monitoring, and incident runbook activation.

## TASK REFERENCES

- `tasks/010-deployment.md`
  - TASK-200 — `azure.yaml` services declaration
  - TASK-201 — `infra/main.parameters.json` env knobs
  - TASK-202 — Pre-deploy checklist automation
  - TASK-203 — `azd up` validation (TEST-001)
  - TASK-204 — Seed + reindex (TEST-002)
  - TASK-205 — Post-deploy smoke matrix (TEST-003/004/005/010)
  - TASK-206 — Playground configuration
  - TASK-207 — Realtime endpoint smoke verification
  - TASK-208 — Per-release quality gate
  - TASK-209 — Pre-public exposure gate
  - TASK-210 — Rollback procedure
  - TASK-211 — Cost monitoring setup
  - TASK-212 — Phase progression (Phase 1 → 2 → 3 per `specs/007-operational-runbook.md §7`)
- Cross-pack dependencies: every preceding pack (001–009).

## SPEC REFERENCES

- `specs/006-testing-strategy.md` — TEST-001/002/003/004/005/006/007/010/011
- `specs/007-operational-runbook.md` — §1 (deploy), §3 (rollback), §5 (cost), §7 (phases), §8 (pre-deploy / post-deploy / per-release / pre-public gates), §9 (incidents)
- `specs/005-security-model.md` — SEC-008, SEC-009, SEC-011

## ADR REFERENCES

- `adr/001-use-microsoft-agent-framework.md` — Hosted Agent deploy target
- `adr/003-use-cosmos-db-for-session-state.md` — Cosmos rollback scope
- `adr/004-use-ai-search-for-question-bank.md` — AI Search rebuilt from Blob

## GOVERNANCE REFERENCES

- `docs/coding-standards.md` — CI/CD discipline
- `docs/ai-agent-development-guidelines.md` — phase progression rules
- `docs/rollback.md` — rollback procedure (this pack authors it)
- `docs/playground.md` — Playground access (this pack authors it)
- `docs/pre-public-gate.md` — pre-public checklist (referenced from 007)

## OBJECTIVE

Deliver the deployment + operational layer that:

1. Declares every deployable service in `azure.yaml` (`quiz-agent`, `seed-loader`) with pre-/post-provision hooks.
2. Centralises env-specific knobs in `infra/main.parameters.json` + `parameters.dev.json` + `parameters.prod.json`.
3. Implements the pre-deploy checklist hook (`make pre-deploy` or `azd hooks` equivalent) that asserts Bicep modules referenced, parameters populated, role assignments enumerable, AppConfig keys present, Key Vault accessible.
4. Validates `azd up` from a clean subscription end-to-end (TEST-001); post-provision hook prints `OK` for every resource.
5. Runs the seed loader (`src/seed/seed_index.py`) using MI; asserts ≥ 90 docs across en/fr/es × 3 topics; logs per-language counts (TEST-002).
6. Runs the post-deploy smoke matrix: text English (TEST-003), text French (TEST-004), voice Spanish (TEST-005), observability (TEST-010). Single pass/fail report per smoke.
7. Confirms the Hosted Agent surfaces in the Foundry Playground; documents the path in `docs/playground.md`.
8. Performs a synthetic Realtime WebRTC round-trip and verifies App Insights logs (TASK-207).
9. Encodes the per-release quality gate: full test suite + TEST-006 per language + TEST-011 per-language eval parity tolerance.
10. Encodes the pre-public exposure gate: APIM quotas active (SEC-011) + retention applied (SEC-008) + LLM-boundary reviewed (SEC-009).
11. Documents and dry-runs the rollback procedure: agent (`azd deploy` of previous tag), index (rebuild from Blob), Cosmos (out-of-scope per ADR-003).
12. Wires cost alerts (50/80/100% of monthly budget) and surfaces "Realtime audio minutes per session" KPI alongside the voice session cap.
13. Manages the Phase 1 → Phase 2 → Phase 3 progression per `specs/007-operational-runbook.md §7`, ending each phase with a documented checkpoint and a passing smoke.

## IMPLEMENTATION RULES

- **`azd up` is the single deployment command.** From a clean subscription, it must succeed end-to-end. Per-environment parameters via `azd env select <env>`.
- **Parameters per env**: `parameters.dev.json` and `parameters.prod.json`; switching `azd env select prod` picks up prod values.
- **Pre-deploy hook (TASK-202)** exits non-zero with a clear message when:
  - A Bicep module is referenced but not present.
  - `parameters.<env>.json` missing a required key.
  - Managed Identity role assignments not enumerable.
  - AppConfig keys (model name, search endpoint, supported languages, voice config) missing.
  - Key Vault not accessible to the deployer.
- **Post-provision hook (TASK-203)** prints `OK` for every resource and asserts:
  - `uami-indexer-*` cannot create an index (403 from a synthetic create call).
  - `uami-agent-*` cannot write to the index (403 from a synthetic write call).
  - No `Owner`/`Contributor`/`User Access Administrator` on runtime UAMIs.
  - All resources have `disableLocalAuth: true` where the property exists.
- **Seed run (TASK-204)** uses Managed Identity (`uami-indexer-*`); asserts per-language counts equal expected; aborts if `Search Service Contributor` is on the running identity.
- **Smoke matrix (TASK-205)** runs after `azd up`:
  - Text English (TEST-003)
  - Text French (TEST-004)
  - Voice Spanish (TEST-005)
  - Observability — assert `grading_event` dimensions (TEST-010)
  - Voice flake retry: retry once with documented note.
- **Realtime smoke (TASK-207)**: synthetic WebRTC connect; short utterance; transcript received; App Insights logs the round-trip within 2 seconds.
- **Per-release quality gate (TASK-208)** rejects a tag if any of: full test suite fail, TEST-006 fail in any language, TEST-011 fail per-language eval parity tolerance. Tag-bypass via local push is forbidden — protect the tag namespace in the platform.
- **Pre-public exposure gate (TASK-209)** rejects `public-ready` tag unless:
  - APIM quotas configured + active (SEC-011).
  - Retention applied to `sessions` (TTL) + transcripts (LAW windows) (SEC-008).
  - "What does the LLM see" boundary doc reviewed (SEC-009).
- **Rollback procedure (TASK-210)**:
  - Agent: `azd deploy` of the previous tag.
  - Index: reindex from authored Blob at the previous tag (Blob is the source of truth; AI Search is rebuilt).
  - Cosmos: **out-of-scope**. Cosmos is the system of record; do NOT roll back data state.
- **Cost monitoring (TASK-211)**: Azure cost alerts at 50/80/100% of monthly budget; "Realtime audio minutes per session" KPI tile populated; voice session cap visible alongside.
- **Phase progression (TASK-212)**:
  - **Phase 1 — PoC core (2–3 days)**: Bicep skeleton (001), one topic ≥10 questions × 3 languages, MAF agent + five tools (004 + 005), Cosmos sessions + AI Search index (002 + 003), Playground text mode (TEST-003).
  - **Phase 2 — Voice + hardening (3–4 days)**: Realtime endpoint (006), answer normaliser + TTS-friendly returns (005 TASK-086/087), conditional-write idempotency (003 TASK-047 + 007 TASK-131), leak tests (009 TASK-160), grading observability (008 TASK-141), MI end-to-end (007 TASK-120/121), App Insights wiring (008 TASK-140).
  - **Phase 3 — Operational polish (2–3 days)**: per-language Foundry Evaluations (009 TASK-167), retention + TTL (003 TASK-050/051), APIM rate limiting (007 TASK-129), runbook + cost dashboard (008 TASK-147 + this pack TASK-211), channel-switch test (009 TASK-171).
  - Each phase ends with a documented checkpoint + a passing smoke. **Feature freeze per phase** — no scope creep across phases.

## OUTPUT FILES

Generate / update:

- `azure.yaml` — declare `quiz-agent` (Hosted Agent target) and `seed-loader` (script target); pre-/post-provision hooks under `infra/hooks/`
- `infra/main.parameters.json` — base parameters
- `infra/main.parameters.dev.json` — dev overrides
- `infra/main.parameters.prod.json` — prod overrides
- `infra/hooks/pre-deploy.sh` — pre-deploy checklist hook (TASK-202)
- `infra/hooks/post-provision.sh` — post-provision asserts (extends 001 if exists)
- `infra/hooks/post-deploy-smoke.sh` — runs TEST-003/004/005/010 after `azd up`
- `Makefile` — `pre-deploy`, `deploy`, `smoke`, `rollback`, `pre-public-check` targets
- `.github/workflows/release-gate.yml` — per-release quality gate (TASK-208) + pre-public gate (TASK-209)
- `docs/playground.md` — project URL, agent ID, access notes
- `docs/rollback.md` — agent + index rollback procedure (Cosmos out of scope)
- `docs/phase-progression.md` — Phase 1/2/3 checklists + checkpoints
- `infra/modules/observability/cost-alerts.bicep` — 50/80/100% budget alerts
- `tests/release/test_release_gate.py` — dry-run release-gate verification
- `tests/release/test_pre_public_gate.py` — dry-run pre-public gate verification

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **NFR-012 / TEST-001**: `azd up` from a clean subscription exits 0; post-provision hook reports `OK` for every resource.
- **TEST-002**: seed loader produces ≥ 90 docs in the index; per-language counts equal expected.
- **TEST-003/004/005/010**: post-deploy smoke matrix all green.
- **Playground**: a reviewer can open the Playground and chat with the agent.
- **Realtime smoke (TASK-207)**: synthetic round-trip within 2 seconds.
- **Per-release gate (TASK-208)**: a deliberate test failure blocks the tag.
- **Pre-public gate (TASK-209)**: missing APIM / retention / boundary-review blocks `public-ready` tagging.
- **Rollback dry-run (TASK-210)**: previous-tag agent + index restored; Cosmos data state unchanged.
- **Cost monitoring (TASK-211)**: alerts fire at the configured thresholds; KPI tile populated.
- **Phase checkpoints (TASK-212)**: each phase ends with a documented checkpoint + a passing smoke; no scope creep.

## FORBIDDEN ACTIONS

- Do NOT skip the post-provision RBAC negative assertions. They are the load-bearing proof of least privilege.
- Do NOT pass `--no-verify`, `--no-gpg-sign`, or any tag-bypass flag in release pipelines.
- Do NOT roll back Cosmos data state. Cosmos is the system of record; rollback is out of scope per ADR-003. Idempotency primitives (etag conditional writes) and durable state mean code rollback alone is safe.
- Do NOT tag `public-ready` without all three pre-public boxes checked (APIM + retention + LLM-boundary review).
- Do NOT enable APIM by default in dev. Toggle via `features:apim`; mandatory only pre-public.
- Do NOT run the full release-pipeline test matrix on every PR. Release tier runs on tag only.
- Do NOT bypass the per-release quality gate by re-running until green. Flake retry is allowed once with a documented note; persistent flake is an incident, not a release path.
- Do NOT seed content using the runtime agent identity (`uami-agent-*`). Seed loader runs as `uami-indexer-*` and aborts if running with `Search Service Contributor`.
- Do NOT skip the Phase 1/2/3 checkpoints. Feature freeze per phase; scope creep across phases is forbidden.
- Do NOT implement runtime code, tool surfaces, or agent logic in this pack. This pack deploys, smokes, and gates. Code lives in 001–009.
- Do NOT bypass the pre-deploy hook to "save time." False confidence is a deploy incident.
- Do NOT include keys or connection strings in `azure.yaml`, parameter files, or hook scripts. App Insights connection string is the documented exception.
- Do NOT run the seed loader against the wrong environment (e.g., production seed loader in dev RG). Hook validates `azd env` matches the deploy target.
- Do NOT modify rollback semantics without updating `docs/rollback.md`. The doc and the script are the same artifact.
