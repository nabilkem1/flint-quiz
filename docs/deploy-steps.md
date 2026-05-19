# Flint Quiz — Deploy Steps

End-to-end playbook for standing up Flint Quiz in a fresh Azure subscription. Validated by a destroy-and-rebuild on `2026-05-18`: `azd down --force --purge` followed by `azd up --no-prompt` brought the entire stack (18 Azure resources + Foundry agent + seeded Search index + seeded Cosmos topics + running sweeper) from zero to ready in **6m 42s** with no manual `az` commands between the two operations.

Cross-references: [`infra/README.md`](../infra/README.md), [`infra/main.bicep`](../infra/main.bicep), [`azure.yaml`](../azure.yaml), [`docs/playground.md`](./playground.md), [`docs/rollback.md`](./rollback.md).

---

## 1. Prerequisites

| Tool | Min version | macOS / Linux | Windows (PowerShell) |
|------|-------------|---------------|----------------------|
| Azure CLI (`az`) | 2.60+ | `brew install azure-cli` | `winget install -e --id Microsoft.AzureCLI` |
| Azure Developer CLI (`azd`) | 1.10+ | `brew install azd` | `winget install -e --id Microsoft.Azd` |
| `jq` (for JSON munging in hooks) | 1.6+ | `brew install jq` | `winget install -e --id jqlang.jq` |
| Bicep CLI | bundled with `az` | auto | auto |
| `python3` (only for the chat CLI / local smoke; not required for deploy) | 3.12+ | `brew install python@3.12` | `winget install -e --id Python.Python.3.12` |

> **Windows shell**: every Bash snippet below has a PowerShell equivalent. `azd` / `az` themselves are cross-platform with identical syntax — only the **shell glue** (variable substitution, command interpolation, `set -a; eval ...` blocks) differs. The hook scripts (`infra/hooks/*.sh`) are Bash; on Windows run `azd up` from **Git Bash** or **WSL** so the hooks fire correctly. PowerShell-only environments will see the hooks skipped — the deploy succeeds but you'll need to run the index PUT + seed-loader trigger manually (see §7).

You also need:

- An Azure subscription where you have **Owner** or **User Access Administrator** (the deploy creates custom RBAC role definitions + role assignments — `Contributor` alone is not enough).
- A region that hosts the Foundry Realtime + chat models (default: `eastus2`).
- A region with AI Search SKU capacity (default: `eastus` — kept separate because `eastus2` Search basic-tier capacity is frequently exhausted).
- Quota for `Microsoft.CognitiveServices/accounts` (Foundry account creation can be capacity-constrained; check via the Azure Portal **Subscription → Usage + quotas**).

Resources NOT required:

- `Microsoft.Web/serverFarms` quota — the sweeper moved off the legacy Functions-on-VM design to a Container Apps Job (commit `f0acf23`).
- Docker locally — image builds use ACR Tasks (`docker.remoteBuild: true` in `azure.yaml`).

---

## 2. One-time auth

```bash
az login                # used by post-provision.sh hook + manual az commands
azd auth login          # used by `azd up` / `azd provision`
```

`az` and `azd` keep separate auth state (`~/.azure/` vs `~/.azure/auth/`); sign in to both with the same account. On personal Microsoft accounts, the second `azd auth login` opens a browser as usual.

---

## 3. Initialise the azd environment

**Bash / zsh (macOS / Linux / Git Bash on Windows):**

```bash
cd /path/to/flint-quiz
azd env new dev          # creates .azure/dev/
```

```bash
# Required
azd env set AZURE_LOCATION eastus2                          # Foundry Realtime region
azd env set TAG_OWNER      "you@example.com"                # used as `owner` resource tag

# Optional overrides (defaults shown)
azd env set SEARCH_LOCATION         eastus
azd env set AZURE_PRINCIPAL_ID      "$(az ad signed-in-user show --query id -o tsv)"
azd env set AZURE_PRINCIPAL_TYPE    User                    # ServicePrincipal in CI
```

Verify:

```bash
azd env get-values | grep -E 'AZURE_LOCATION|TAG_OWNER|SEARCH_LOCATION'
```

**PowerShell (Windows):**

```powershell
cd C:\path\to\flint-quiz
azd env new dev          # creates .azure\dev\
```

```powershell
# Required
azd env set AZURE_LOCATION eastus2
azd env set TAG_OWNER      "you@example.com"

# Optional overrides
azd env set SEARCH_LOCATION         eastus
$oid = az ad signed-in-user show --query id -o tsv
azd env set AZURE_PRINCIPAL_ID      $oid
azd env set AZURE_PRINCIPAL_TYPE    User
```

Verify:

```powershell
azd env get-values | Select-String 'AZURE_LOCATION|TAG_OWNER|SEARCH_LOCATION'
```

> If `AZURE_PRINCIPAL_ID` isn't set explicitly, `azd up` resolves it automatically from your `azd auth login` identity.

---

## 4. Run the full deploy

```bash
azd up
```

That's the whole thing. On a clean subscription expect **~7-12 minutes** (heavy on Cosmos + Foundry account creation + Container Apps image builds via ACR Tasks).

The full lifecycle `azd up` orchestrates:

1. **`infra/hooks/pre-deploy.sh`** — preflight checklist:
   - Required env vars set.
   - All Bicep modules exist on disk.
   - Parameter file covers every required key.
   - **Stale `SERVICE_*_IMAGE_NAME` guard**: if a previous `azd down` left image tags pointing at a now-deleted ACR, the hook probes the manifest and clears the env var so the bootstrap fallback applies. This is what makes destroy-and-rebuild a one-liner.
2. **Bicep provisioning** — creates ~18 resources end-to-end:
   - Resource group `fq-<env>-rg`
   - 3× User-Assigned Managed Identities (`uami-{agent,indexer,deploy}`)
   - Log Analytics + Application Insights + workbooks/alerts
   - Key Vault (RBAC mode, purge protection)
   - Storage account (`allowSharedKeyAccess=false`, audit-archive immutability)
   - Cosmos DB account + database + 4 containers (sessions/users/topics/audit), `disableLocalAuth=true`
   - AI Search service (`disableLocalAuth=true`)
   - App Configuration store (`disableLocalAuth=true`)
   - Container Registry (Basic SKU, admin disabled)
   - Container Apps Environment + 3 service hosts:
     - `quiz-agent` Container App
     - `seed-loader` Container Apps Job (manual trigger)
     - `sweeper` Container Apps Job (cron `*/1 * * * *`)
   - Microsoft Foundry account + project + 2 model deployments (`gpt-realtime` + `gpt-4o-mini`)
   - **Custom RBAC role definition** `Foundry Agents Writer (<account>)` so `uami-agent-*` can write agent versions.
   - 12+ role assignments (per-resource scope, least privilege)
3. **`infra/hooks/post-provision.sh`** — declarative-by-REST steps + sanity checks:
   - Health probes (`az ... show` on every resource).
   - **Synonym maps** (3 × per-language) PUT to AI Search via REST.
   - **`questions` index** PUT to AI Search via REST.
   - Negative least-privilege probes (`uami-indexer-*` MUST NOT be able to create-index; `uami-agent-*` MUST NOT be able to write docs).
4. **ACR Tasks remote builds** for the 3 service images (parallel, ~3 min each).
5. **`azd deploy`** updates the 3 Container Apps / Jobs to the freshly-built images.
6. **`infra/hooks/post-deploy-smoke.sh`** — release-gate smoke matrix:
   - Triggers the `seed-loader` Container Apps Job remotely (`az containerapp job start --wait`). The job runs `python -m src.seed.seed_index && python -m src.seed.seed_topics` — chained so a single firing seeds both AI Search docs (~90) AND Cosmos topic catalog rows (3).
   - Runs the in-process pytest smoke matrix (TEST-003 EN, TEST-004 FR, TEST-005 ES).
   - Best-effort App Insights `grading_event` observability check (informational on first deploy — needs real user traffic to produce a signal).

A green `azd up` finishes with:

```
SUCCESS: Your application was provisioned and deployed to Azure in N minutes M seconds.
```

---

## 5. Validation

After `azd up` returns, sanity-check the deploy.

**Bash / zsh:**

```bash
set -a; eval "$(azd env get-values | sed 's/^/export /')"; set +a

# Resource count (expect 18 on a fresh deploy)
az resource list -g "$AZURE_RESOURCE_GROUP" --query "length(@)" -o tsv

# Sweeper cron firing every minute
az containerapp job execution list -n "$SWEEPER_JOB_NAME" -g "$AZURE_RESOURCE_GROUP" -o table | head

# Seed-loader: latest execution should be Succeeded
az containerapp job execution list -n "$SEED_LOADER_JOB_NAME" -g "$AZURE_RESOURCE_GROUP" -o table | head

# Agent revision active + healthy
az containerapp revision list -n "$QUIZ_AGENT_CONTAINER_APP_NAME" -g "$AZURE_RESOURCE_GROUP" \
  --query "[?properties.active].{name:name, health:properties.healthState}" -o table

# AI Search index doc count
TOKEN=$(az account get-access-token --resource https://search.azure.com --query accessToken -o tsv)
curl -s "$SEARCH_ENDPOINT/indexes/questions/docs/\$count?api-version=2024-07-01" \
  -H "Authorization: Bearer $TOKEN"
# expect: 90 (30 per language × 3 languages)
```

**PowerShell:**

```powershell
# Load azd env vars into the current session
azd env get-values | ForEach-Object {
  if ($_ -match '^([A-Z_][A-Z0-9_]*)="?(.*?)"?$') {
    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
  }
}

# Resource count (expect 18 on a fresh deploy)
az resource list -g $env:AZURE_RESOURCE_GROUP --query "length(@)" -o tsv

# Sweeper cron firing every minute
az containerapp job execution list -n $env:SWEEPER_JOB_NAME -g $env:AZURE_RESOURCE_GROUP -o table | Select-Object -First 10

# Seed-loader: latest execution should be Succeeded
az containerapp job execution list -n $env:SEED_LOADER_JOB_NAME -g $env:AZURE_RESOURCE_GROUP -o table | Select-Object -First 10

# Agent revision active + healthy
az containerapp revision list -n $env:QUIZ_AGENT_CONTAINER_APP_NAME -g $env:AZURE_RESOURCE_GROUP `
  --query "[?properties.active].{name:name, health:properties.healthState}" -o table

# AI Search index doc count
$token = az account get-access-token --resource https://search.azure.com --query accessToken -o tsv
Invoke-RestMethod -Uri "$($env:SEARCH_ENDPOINT)/indexes/questions/docs/`$count?api-version=2024-07-01" `
  -Headers @{ Authorization = "Bearer $token" }
# expect: 90 (30 per language × 3 languages)
```

For an interactive end-to-end test, follow [`docs/playground.md`](./playground.md):

1. `azd env get-values | grep FOUNDRY_PROJECT_ENDPOINT` — note the URL.
2. Azure Portal → Foundry project → Agents → `fq-<env>-agent` → **Try in Playground**.
3. Send a message like _"Start a 3-question quiz on Azure Networking in English."_

The agent should call `list_topics` → `start_quiz` → present Q1 with options.

---

## 6. Tear down

```bash
azd down --force --purge
```

- `--force` skips confirmation prompts.
- `--purge` purges soft-deleted Key Vault + Cognitive Services (Foundry) + AppConfig + Log Analytics. Without this, re-provisioning in the same subscription / region will fail on name conflicts (90-day soft-delete window by default).

After a successful tear-down the `.azure/<env>/.env` file retains some `SERVICE_*_IMAGE_NAME` references from the just-deleted ACR. The `pre-deploy.sh` hook detects this on the next `azd up` and clears them automatically — no manual `.env` editing required.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `azd up` errors out citing `infra.parameters.location` (or `tagOwner`) | Required env vars not set | Re-run `§3` setup; `AZURE_LOCATION` and `TAG_OWNER` are the two non-defaulted params. |
| `ResourceGroupNotFound` mid-provision (transient, after just creating the RG) | ARM eventually-consistent cache | Re-run `azd up` — bicep is idempotent; previous resources are detected as already-created. |
| `SpecialFeatureOrQuotaIdRequired … gpt-realtime` | Foundry quota for the model SKU not granted in your subscription | Request via Azure Portal → Subscription → Usage + quotas → Cognitive Services. Falling back to `gpt-4o-mini` for the agent is already wired (see `CHAT_MODEL_DEPLOYMENT_NAME` param). |
| `InsufficientResourcesAvailable` on AI Search create | Regional capacity exhausted | `azd env set SEARCH_LOCATION <region>` (try `westus2`, `swedencentral`), re-run `azd provision`. |
| Sweeper first 1-3 cron executions show `Failed` | Bootstrap-image race — the bicep creates the CAJ before `azd deploy sweeper` pushes the real image | Expected on a brand-new env; the next firing on the real image succeeds and stays green. No action needed. |
| `MANIFEST_UNKNOWN` from Container App / Job create | Stale `SERVICE_*_IMAGE_NAME` in azd env pointing at a deleted ACR | `pre-deploy.sh` is supposed to clear these; if it doesn't, edit `.azure/<env>/.env` and remove the `SERVICE_*` lines, then re-run. |
| Post-provision: `PUT /indexes/questions → 403` | Operator's CLI principal lacks `Search Service Contributor` on the search service | The bicep adds this assignment (`deployerHumanSearchContributor` in `rbac.bicep`); RBAC propagation can take ~60s — re-run `azd provision` or the hook standalone. |
| Post-deploy smoke: `seed-loader job failed` and logs show `The index 'questions' was not found` | `post-provision.sh` was skipped or its index PUT failed | Re-run `infra/hooks/post-provision.sh` directly; once the index exists, re-trigger the CAJ: `az containerapp job start -n "$SEED_LOADER_JOB_NAME" -g "$AZURE_RESOURCE_GROUP"`. |
| `Foundry Agents Writer` role assignment fails | Bicep deployer principal lacks Owner / User Access Administrator on the Foundry account scope | Confirm your account holds one of those roles on the subscription (or scope the assignment manually as a temporary workaround). |
| **Windows / native PowerShell only** — `azd up` reports SUCCESS but post-provision skipped; index missing | Bash hooks (`infra/hooks/*.sh`) don't execute under cmd.exe / native PowerShell | Either (a) re-run `azd up` from **Git Bash** or **WSL**, OR (b) run the manual PowerShell fallback below. |

### Windows manual fallback (PowerShell-only environments)

If the Bash hooks didn't fire and you need to seed the index + topics by hand:

```powershell
# Load azd env
azd env get-values | ForEach-Object {
  if ($_ -match '^([A-Z_][A-Z0-9_]*)="?(.*?)"?$') {
    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
  }
}

# Grant yourself Search Service Contributor (bicep does this, but if you bypassed the hook you may need to wait for propagation)
$searchId = az search service show -n $env:SEARCH_SERVICE_NAME -g $env:AZURE_RESOURCE_GROUP --query id -o tsv
$me       = az ad signed-in-user show --query id -o tsv
az role assignment create --assignee-object-id $me --assignee-principal-type User `
  --role "Search Service Contributor" --scope $searchId
Start-Sleep -Seconds 60     # wait for RBAC propagation

# PUT the three synonym maps
$token = az account get-access-token --resource https://search.azure.com --query accessToken -o tsv
$headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }
foreach ($lang in 'en','fr','es') {
  $raw = Get-Content "infra/scripts/synonyms-$lang.json" | ConvertFrom-Json
  $raw.synonyms = ($raw.synonyms -join "`n")
  $body = $raw | ConvertTo-Json -Compress -Depth 10
  Invoke-RestMethod -Method PUT `
    -Uri "$($env:SEARCH_ENDPOINT)/synonymmaps/topic-synonyms-$lang`?api-version=2024-07-01" `
    -Headers $headers -Body $body
}

# PUT the questions index
$schema = Get-Content "infra/scripts/questions-index-schema.json" -Raw
Invoke-RestMethod -Method PUT `
  -Uri "$($env:SEARCH_ENDPOINT)/indexes/questions`?api-version=2024-07-01" `
  -Headers $headers -Body $schema

# Trigger the seed-loader CAJ (chains seed_index + seed_topics)
az containerapp job start -n $env:SEED_LOADER_JOB_NAME -g $env:AZURE_RESOURCE_GROUP
```

---

## 8. Known trade-offs

These are intentional and documented in the relevant commit messages — they don't block a working deploy:

- **App Configuration keyValues seeded out-of-band**: the runtime has hard-coded defaults for the four keys (`model:deploymentName`, `search:endpoint`, `languages:supported`, `features:apim`); the bicep deliberately does NOT declare keyValues because the AppConfig `disableLocalAuth=true` + pass-through data-plane wiring races with RBAC propagation. Revisit if any of the four becomes load-bearing.
- **First-deploy sweeper bootstrap race** (see §7): the sweeper CAJ provisions with the public `containerapps-helloworld` image and is immediately Schedule-active. The first 1-3 cron firings fail until `azd deploy sweeper` swaps in the real image. Cosmetic only.
- **Cosmos UAMI role is `Built-in Data Contributor` at account scope**: the original design called for a custom role with per-container DataActions (`sessions`/`users`/`audit` rw, `topics` r/o). Account-scope is the v1 design.
- **Search index created via `post-provision.sh` REST PUT, not bicep**: a direct `Microsoft.Search/searchServices/indexes` resource returned opaque `BadRequest` on this schema; the legacy `deploymentScripts`-based module fails on personal MSA subscriptions (identity-endpoint URI parse error). REST PUT works on every subscription.
- **The `grading_event` observability check (TEST-010) is informational on a fresh deploy** — it needs real user traffic to produce a signal. Starts emitting PASS/FAIL once a real quiz roundtrips.

---

## 9. What's next

After a successful deploy:

- [`docs/playground.md`](./playground.md) — manual quiz testing via the Foundry Playground.
- [`docs/observability.md`](./observability.md) — workbooks + alerts wired against the `sweeper.*` and `grading_event` metrics.
- [`docs/rollback.md`](./rollback.md) — incident response runbook.
- Tighten the Cosmos custom role and add an immutability policy on the audit-archive container (see open trade-offs above).
