# DEV-STORY PROMPT — TASK-001 INFRASTRUCTURE (Azure Foundation)

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 1 — Infrastructure Foundation
**Current Task Pack**: 001-infrastructure (Azure Bicep IaC, deployable via `azd up`)
**Scope**: Provision every Azure resource the v1 quiz system depends on. Runtime configuration (containers, indexes, agent code) lives in 002–006. This pack stops at "resources exist, identities are wired, role assignments resolve."

## TASK REFERENCES

- `tasks/001-infrastructure.md`
  - TASK-001 — azd + Bicep skeleton
  - TASK-002 — Resource group, naming conventions, tagging
  - TASK-003 — Azure AI Foundry project (UAMI-only, no SAMI)
  - TASK-004 — Cosmos DB account (`disableLocalAuth: true`)
  - TASK-005 — Azure AI Search (S1, `disableLocalAuth: true`)
  - TASK-006 — Blob Storage (`allowSharedKeyAccess: false`)
  - TASK-007 — Key Vault (RBAC mode, purge protection on)
  - TASK-008 — App Configuration (`disableLocalAuth: true`, seeded keys)
  - TASK-009 — Application Insights + Log Analytics workspace
  - TASK-010 — User-Assigned Managed Identity (UAMI)
  - TASK-011 — RBAC assignments (three runtime identities: `uami-agent-*`, `uami-indexer-*`, `uami-deploy-*`)
  - TASK-012 — Hosted Agent runtime in Foundry Agent Service
  - TASK-013 — Realtime (voice) endpoint
  - TASK-014 — `azd up` end-to-end validation

## SPEC REFERENCES

- `specs/002-system-architecture.md` — §6.2 (Hosted Agent runtime), §9 (Realtime endpoint)
- `specs/005-security-model.md` — SEC-003, SEC-004, SEC-005, SEC-010, SEC-011, SEC-013
- `specs/007-operational-runbook.md` — §1 (deploy), §8.1 (pre-deploy checklist)

## ADR REFERENCES

- `adr/001-use-microsoft-agent-framework.md` — Hosted Agent in Foundry
- `adr/003-use-cosmos-db-for-session-state.md` — Cosmos account requirements
- `adr/004-use-ai-search-for-question-bank.md` — AI Search S1 requirements

## GOVERNANCE REFERENCES

- `docs/coding-standards.md` — Bicep style, naming tokens, tagging
- `docs/ai-agent-development-guidelines.md` — UAMI vs SAMI, least-privilege RBAC
- `docs/secrets.md` — zero connection strings; nothing in env that is not non-secret config
- `docs/governance-consistency-audit.md` — RBAC split between control plane (CI) and data plane (indexer)

## OBJECTIVE

Author Bicep IaC that provisions, in a single `azd up`:

- Resource group with deterministic naming + mandatory tags
- AI Foundry hub + project (UAMI-only, no SAMI)
- Cosmos DB account (SQL API, local auth disabled)
- Azure AI Search S1 (semantic search, local auth disabled)
- Storage account (StorageV2, shared key disabled, soft-delete 7d) with `questions` container + per-language virtual folders
- Key Vault (RBAC mode, soft-delete + purge protection)
- App Configuration (local auth disabled) seeded with `model:deploymentName`, `search:endpoint`, `languages:supported`, `features:apim`
- Application Insights (workspace-based) + Log Analytics workspace
- Three User-Assigned Managed Identities (`uami-agent-*`, `uami-indexer-*`, `uami-deploy-*`) with least-privilege RBAC scoped per-resource
- Hosted Agent in Foundry with UAMI attached
- Realtime endpoint enabled on the Hosted Agent with per-language voice configuration
- `azd up` post-provision hook that asserts every resource is healthy

## IMPLEMENTATION RULES

- **Single deployment command**: `azd up` from a clean subscription must succeed end-to-end.
- **Entry point**: `infra/main.bicep` is subscription-scoped; per-resource modules live under `infra/modules/`.
- **Naming token**: `${prefix}-${environment}-${resource}` (e.g., `fq-dev-cosmos`); add `uniqueString(resourceGroup().id)` suffix where global uniqueness is required (Storage, Key Vault).
- **Mandatory tags on every resource**: `{environment, owner, costCenter, app="flint-quiz"}`.
- **Disable local auth everywhere it exists**: Cosmos (`disableLocalAuth: true`), AI Search (`disableLocalAuth: true`), AppConfig (`disableLocalAuth: true`), Storage (`allowSharedKeyAccess: false`).
- **UAMI only**: do NOT enable SAMI on the Foundry project or the Hosted Agent. Attach `uami-agent-*` to the Hosted Agent.
- **Least-privilege RBAC**, scoped per-resource (never RG, never subscription):
  - `uami-agent-*`: Cosmos Built-in Data Contributor (custom data-plane role, restricted to `sessions`, `users`, `audit`, `topics`), Search Index Data **Reader** (read-only), Key Vault Secrets User, App Configuration Data Reader, Monitoring Metrics Publisher.
  - `uami-indexer-*`: Search Index Data **Contributor** (writes data; does NOT include Search Service Contributor), Storage Blob Data Reader on `authoring` container.
  - `uami-deploy-*`: Contributor (env RG, PIM-elevated in prod), Search Service Contributor (CI is the only principal that creates/deletes the index).
- **Index lifecycle is Bicep-owned**, not runtime-owned. Index creation lives in a Bicep deployment script using `uami-deploy-*`.
- **Connection strings are forbidden** in Bicep outputs, code, env files. App Insights connection string is the documented exception (per Microsoft guidance, not a secret).
- **Region pin**: pick a region that supports Foundry Realtime API; verify against the eligibility table before locking in.
- **Cosmos** autoscale enabled; single-region first; allow public network for v1 (reserve VNET integration for v2).
- **Key Vault** purge protection ON (cannot be undone — confirm in review).
- **Realtime endpoint**: configure max session length cap (NFR-013) and supported voices for `en`/`fr`/`es`.

## OUTPUT FILES

Generate:

- `azure.yaml` declaring services (`quiz-agent: hosted-agent`, `seed-loader: script`).
- `infra/main.bicep` (subscription-scoped entry point, single RG module).
- `infra/main.parameters.json` (and `parameters.dev.json`, `parameters.prod.json`) with placeholders for `environmentName`, `location`, `supportedLanguages`, `modelDeploymentName`, `cosmosSessionsTtlDays`, `auditTtlDays`, `voiceMaxSessionMinutes`, `voiceIdleSeconds`, `features:apim`.
- `infra/modules/resource-group.bicep`
- `infra/modules/foundry-project.bicep`
- `infra/modules/cosmos.bicep`
- `infra/modules/search.bicep`
- `infra/modules/storage.bicep` (creates `questions` container with `en/`, `fr/`, `es/` virtual folders)
- `infra/modules/keyvault.bicep`
- `infra/modules/appconfig.bicep` (seeds initial keys via `keyValues` child resources)
- `infra/modules/observability.bicep` (LAW + App Insights workspace-based; diagnostic settings wired to Foundry project)
- `infra/modules/uami.bicep` (creates the three UAMIs and outputs their `principalId`/`clientId`/`resourceId`)
- `infra/modules/rbac.bicep` (or per-resource role-assignment modules) — implements the three-identity matrix above
- `infra/modules/hosted-agent.bicep` (attaches `uami-agent-*`, wires AppInsights + AppConfig)
- `infra/modules/realtime.bicep` (enables Realtime API, configures voices + session-length cap)
- `infra/hooks/post-provision.sh` — Bash hook that runs `az ... show` for every resource and prints `OK` / `FAIL` per resource; asserts:
  - `uami-indexer-*` cannot create an index (post-deploy 403 assertion).
  - `uami-agent-*` cannot write to the index (post-deploy 403 assertion).
  - No `Owner`/`Contributor`/`User Access Administrator` on runtime UAMIs.
- `infra/README.md` documenting §3.1 (UAMI rationale), §10.x (RBAC matrix), §11.x (observability wiring), §12.x (retention surface).

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **NFR-012**: `azd up` from a clean subscription deploys every resource; exits 0.
- **NFR-007**: every Azure SDK call from runtime code will use `DefaultAzureCredential`; no connection strings emitted in Bicep outputs.
- **NFR-008**: App Insights diagnostic settings wire Foundry project tracing into the workspace-based App Insights.
- **SEC-004**: Cosmos `disableLocalAuth: true`; no keys retrievable.
- **SEC-005**: RBAC scoped per-resource; runtime identities cannot escalate (post-deploy assertions enforce).
- **SEC-013**: Key Vault in RBAC mode; soft-delete + purge protection enabled.
- **TEST-001**: `azd provision --preview` returns a valid plan; `azd up` post-provision hook reports `OK` for every resource.
- **Idempotency**: re-running `azd provision` against a deployed env is a no-op (Bicep is declarative).
- **Forward compatibility**: re-creating the Hosted Agent preserves role assignments (UAMI ensures principal IDs survive).

## FORBIDDEN ACTIONS

- Do NOT enable system-assigned identities (SAMI) on the Foundry project, the Hosted Agent, or any resource — UAMI only.
- Do NOT use access policies on Key Vault — RBAC mode only.
- Do NOT emit any connection string, account key, SAS token, or shared key as a Bicep output, AppConfig value, or env var. Exception: App Insights connection string (non-secret per Microsoft guidance).
- Do NOT grant `Owner`, `Contributor`, or `User Access Administrator` to runtime UAMIs (`uami-agent-*`, `uami-indexer-*`).
- Do NOT grant `Search Service Contributor` to `uami-agent-*` or `uami-indexer-*`. Only `uami-deploy-*` may hold it.
- Do NOT grant `Search Index Data Contributor` to `uami-agent-*` (runtime is read-only on the index).
- Do NOT create Cosmos containers, AI Search indexes, or seed AppConfig values that belong to later task packs (002, 003). Index/container schema lives in 002 and 003; seed content lives in 002.
- Do NOT implement agent code, tool code, or data-access code — those live in 004 and 005.
- Do NOT scope role assignments to the resource group or subscription when a smaller scope (per-resource) is available.
- Do NOT pin a region that lacks Foundry Realtime API availability.
- Do NOT skip the `--confirm`-style guards on destructive operations (e.g., purge protection means a re-deploy with the same Key Vault name is blocked for 90 days post-delete — accept this).
- Do NOT bypass the post-provision hook's negative RBAC assertions (the 403 checks are the load-bearing proof of least privilege).
