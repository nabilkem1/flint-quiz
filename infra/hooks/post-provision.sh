#!/usr/bin/env bash
# Flint Quiz — post-provision validation hook.
#
# Runs after `azd up` succeeds. Two responsibilities:
#
#   (1) Health probe — `az ... show` every resource the env depends on and
#       print OK / FAIL per resource. Any FAIL exits non-zero so azd surfaces
#       it as a deploy failure.
#
#   (2) Negative RBAC assertions — the load-bearing proof that runtime
#       identities cannot escalate:
#         a) uami-indexer-* CANNOT create an AI Search index (expect 403)
#         b) uami-agent-*   CANNOT write a document to the index   (expect 403)
#         c) Neither runtime UAMI holds Owner / Contributor / User Access
#            Administrator on any scope.
#
# Per FORBIDDEN ACTIONS in 001-infrastructure.prompt.md: do NOT bypass these
# assertions. If one fails, fix the RBAC — do not weaken the check.
#
# Required env (populated by `azd env get-values` after main.bicep outputs):
#   AZURE_RESOURCE_GROUP
#   AZURE_LOCATION
#   AZURE_SUBSCRIPTION_ID  (set by azd)
#   APP_CONFIG_ENDPOINT
#   KEY_VAULT_URI / KEY_VAULT_NAME
#   COSMOS_ACCOUNT_NAME / COSMOS_ENDPOINT
#   SEARCH_SERVICE_NAME / SEARCH_ENDPOINT
#   STORAGE_ACCOUNT_NAME / BLOB_ENDPOINT
#   FOUNDRY_PROJECT_NAME
#   AGENT_NAME
#   UAMI_AGENT_CLIENT_ID / UAMI_INDEXER_CLIENT_ID / UAMI_DEPLOY_CLIENT_ID

set -uo pipefail

FAIL_COUNT=0
PASS_COUNT=0

color_ok() { printf '\033[32m%s\033[0m' "$1"; }
color_fail() { printf '\033[31m%s\033[0m' "$1"; }
color_warn() { printf '\033[33m%s\033[0m' "$1"; }

ok() {
  printf '  [%s] %s\n' "$(color_ok 'OK  ')" "$1"
  PASS_COUNT=$((PASS_COUNT + 1))
}
fail() {
  printf '  [%s] %s\n' "$(color_fail 'FAIL')" "$1"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}
section() {
  printf '\n== %s ==\n' "$1"
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    fail "missing env: ${name}"
    return 1
  fi
}

# ----------------------------------------------------------------------------
section 'Required environment values'
# ----------------------------------------------------------------------------
for v in AZURE_RESOURCE_GROUP AZURE_LOCATION AZURE_SUBSCRIPTION_ID \
         APP_CONFIG_ENDPOINT KEY_VAULT_NAME COSMOS_ACCOUNT_NAME \
         SEARCH_SERVICE_NAME STORAGE_ACCOUNT_NAME FOUNDRY_ACCOUNT_NAME \
         FOUNDRY_PROJECT_NAME MODEL_DEPLOYMENT_NAME \
         UAMI_AGENT_CLIENT_ID UAMI_INDEXER_CLIENT_ID UAMI_DEPLOY_CLIENT_ID; do
  if [[ -n "${!v:-}" ]]; then
    ok "env ${v} present"
  else
    fail "env ${v} missing"
  fi
done

# ----------------------------------------------------------------------------
section 'Resource health probes'
# ----------------------------------------------------------------------------

probe() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    ok "${label}"
  else
    fail "${label}"
  fi
}

probe 'Resource group'       az group show -n "${AZURE_RESOURCE_GROUP}"
probe 'Key Vault'            az keyvault show -n "${KEY_VAULT_NAME}" -g "${AZURE_RESOURCE_GROUP}"
probe 'App Configuration'    az appconfig show -n "${APP_CONFIG_NAME:-}" -g "${AZURE_RESOURCE_GROUP}"
probe 'Storage account'      az storage account show -n "${STORAGE_ACCOUNT_NAME}" -g "${AZURE_RESOURCE_GROUP}"
probe 'Cosmos account'       az cosmosdb show -n "${COSMOS_ACCOUNT_NAME}" -g "${AZURE_RESOURCE_GROUP}"
# `az search service show` dropped --service-name; current CLI uses -n.
probe 'AI Search service'    az search service show -n "${SEARCH_SERVICE_NAME}" -g "${AZURE_RESOURCE_GROUP}"
probe 'Application Insights' az monitor app-insights component show --app "${APP_INSIGHTS_NAME:-}" -g "${AZURE_RESOURCE_GROUP}"
probe 'Log Analytics'        az monitor log-analytics workspace show -n "${LOG_ANALYTICS_NAME:-}" -g "${AZURE_RESOURCE_GROUP}"

# Foundry is now a Microsoft.CognitiveServices/accounts (kind=AIServices) per
# https://learn.microsoft.com/en-us/azure/foundry/how-to/create-resource-template
probe 'Foundry account'      az cognitiveservices account show -n "${FOUNDRY_ACCOUNT_NAME}" -g "${AZURE_RESOURCE_GROUP}"

# Foundry project (child of the account). No dedicated `az foundry project`
# command yet — query via the generic resource list and filter by name.
if az resource list -g "${AZURE_RESOURCE_GROUP}" \
     --resource-type 'Microsoft.CognitiveServices/accounts/projects' \
     --query "[?name=='${FOUNDRY_ACCOUNT_NAME}/${FOUNDRY_PROJECT_NAME}'] | length(@)" -o tsv 2>/dev/null \
     | grep -q '^1$'; then
  ok 'Foundry project'
else
  fail 'Foundry project'
fi

# Model deployment (Realtime/Hosted Agent are inert without it).
probe 'Model deployment' az cognitiveservices account deployment show \
  -n "${FOUNDRY_ACCOUNT_NAME}" -g "${AZURE_RESOURCE_GROUP}" \
  --deployment-name "${MODEL_DEPLOYMENT_NAME}"

# ----------------------------------------------------------------------------
section 'Posture assertions (positive)'
# ----------------------------------------------------------------------------

# az CLI TSV output for booleans is inconsistent across versions / resource
# types (some return `true`/`false`, others `True`/`False`). Normalise.
# Use `tr` for portability — macOS ships bash 3.x which lacks ${v,,}.
to_lower() { printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]'; }
is_true()  { [[ "$(to_lower "${1:-}")" == "true"  ]]; }
is_false() { [[ "$(to_lower "${1:-}")" == "false" ]]; }

# Cosmos: local auth disabled
if is_true "$(az cosmosdb show -n "${COSMOS_ACCOUNT_NAME}" -g "${AZURE_RESOURCE_GROUP}" --query disableLocalAuth -o tsv 2>/dev/null)"; then
  ok 'Cosmos disableLocalAuth=true (SEC-004)'
else
  fail 'Cosmos disableLocalAuth is NOT true — SEC-004 violation'
fi

# AI Search: local auth disabled
if is_true "$(az search service show -n "${SEARCH_SERVICE_NAME}" -g "${AZURE_RESOURCE_GROUP}" --query disableLocalAuth -o tsv 2>/dev/null)"; then
  ok 'AI Search disableLocalAuth=true'
else
  fail 'AI Search disableLocalAuth is NOT true'
fi

# AppConfig: local auth disabled
if is_true "$(az appconfig show -n "${APP_CONFIG_NAME:-}" -g "${AZURE_RESOURCE_GROUP}" --query disableLocalAuth -o tsv 2>/dev/null)"; then
  ok 'App Configuration disableLocalAuth=true'
else
  fail 'App Configuration disableLocalAuth is NOT true'
fi

# Storage: shared key disabled
if is_false "$(az storage account show -n "${STORAGE_ACCOUNT_NAME}" -g "${AZURE_RESOURCE_GROUP}" --query allowSharedKeyAccess -o tsv 2>/dev/null)"; then
  ok 'Storage allowSharedKeyAccess=false'
else
  fail 'Storage allowSharedKeyAccess is NOT false'
fi

# Foundry: local auth disabled (Entra-only)
if is_true "$(az cognitiveservices account show -n "${FOUNDRY_ACCOUNT_NAME}" -g "${AZURE_RESOURCE_GROUP}" --query properties.disableLocalAuth -o tsv 2>/dev/null)"; then
  ok 'Foundry account disableLocalAuth=true (Entra-only)'
else
  fail 'Foundry account disableLocalAuth is NOT true'
fi

# Key Vault: RBAC mode + purge protection
KV_JSON="$(az keyvault show -n "${KEY_VAULT_NAME}" -g "${AZURE_RESOURCE_GROUP}" -o json 2>/dev/null)"
if [[ "$(echo "${KV_JSON}" | jq -r '.properties.enableRbacAuthorization')" == "true" ]]; then
  ok 'Key Vault enableRbacAuthorization=true (RBAC mode, SEC-013)'
else
  fail 'Key Vault is NOT in RBAC mode'
fi
if [[ "$(echo "${KV_JSON}" | jq -r '.properties.enablePurgeProtection')" == "true" ]]; then
  ok 'Key Vault purge protection ON (SEC-013)'
else
  fail 'Key Vault purge protection is NOT enabled'
fi

# ----------------------------------------------------------------------------
section 'Posture assertions (negative — least privilege)'
# ----------------------------------------------------------------------------
# These are the load-bearing checks. Per FORBIDDEN ACTIONS, do NOT bypass them.
# Each asserts that a runtime UAMI does NOT hold a privileged role anywhere.

PRIV_ROLES_REGEX='^(Owner|Contributor|User Access Administrator)$'

check_no_privileged_roles() {
  local client_id="$1"
  local label="$2"
  local sp_object_id
  sp_object_id="$(az ad sp show --id "${client_id}" --query id -o tsv 2>/dev/null || true)"
  if [[ -z "${sp_object_id}" ]]; then
    fail "${label}: cannot resolve service principal for clientId=${client_id}"
    return
  fi
  local roles
  roles="$(az role assignment list --assignee-object-id "${sp_object_id}" \
            --assignee-principal-type ServicePrincipal --all \
            --query '[].roleDefinitionName' -o tsv 2>/dev/null || true)"
  if echo "${roles}" | grep -E "${PRIV_ROLES_REGEX}" >/dev/null; then
    fail "${label}: holds at least one of Owner/Contributor/UAA — escalation risk"
  else
    ok "${label}: no Owner/Contributor/User Access Administrator anywhere"
  fi
}

check_no_privileged_roles "${UAMI_AGENT_CLIENT_ID}"   'uami-agent-*'
check_no_privileged_roles "${UAMI_INDEXER_CLIENT_ID}" 'uami-indexer-*'
# uami-deploy-* IS allowed Contributor on the env RG — only verify it is NOT
# Owner / UAA, and not subscription-scoped.
DEPLOY_SP="$(az ad sp show --id "${UAMI_DEPLOY_CLIENT_ID}" --query id -o tsv 2>/dev/null || true)"
if [[ -n "${DEPLOY_SP}" ]]; then
  DEPLOY_ROLES_JSON="$(az role assignment list --assignee-object-id "${DEPLOY_SP}" \
                      --assignee-principal-type ServicePrincipal --all -o json 2>/dev/null || echo '[]')"
  if echo "${DEPLOY_ROLES_JSON}" | jq -e '.[] | select(.roleDefinitionName == "Owner" or .roleDefinitionName == "User Access Administrator")' >/dev/null; then
    fail 'uami-deploy-*: holds Owner or User Access Administrator (forbidden)'
  else
    ok 'uami-deploy-*: no Owner / UAA assignments'
  fi
  if echo "${DEPLOY_ROLES_JSON}" | jq -e '.[] | select(.scope | test("^/subscriptions/[^/]+$"))' >/dev/null; then
    fail 'uami-deploy-*: holds subscription-scoped assignment (env-RG-scope only)'
  else
    ok 'uami-deploy-*: no subscription-scoped role assignments'
  fi
fi

# ---- 403 assertions: indexer cannot create / agent cannot write -----------
# `az account get-access-token --client-id <uami-client-id>` only works when
# the host machine has the named UAMI attached (Azure VM, AKS pod, App
# Service, Cloud Shell with MI). From a developer laptop these calls fail
# with "no Managed Identity endpoint found" — so we SKIP these assertions
# locally and defer them to CI, which runs as uami-deploy-* with federation.
#
# CI behaviour: in GitHub Actions / Azure DevOps with az-login@v2 + the
# uami-deploy-* federated credential, the IMDS endpoint is available and
# these probes execute fully.

IS_AZURE_HOST=0
if [[ -n "${IDENTITY_ENDPOINT:-}" ]] || [[ -n "${MSI_ENDPOINT:-}" ]] || \
   curl -s -m 2 -H 'Metadata: true' \
     'http://169.254.169.254/metadata/instance?api-version=2021-02-01' \
     >/dev/null 2>&1; then
  IS_AZURE_HOST=1
fi

if [[ "${IS_AZURE_HOST}" -eq 0 ]]; then
  echo "  [$(color_warn 'SKIP')] negative RBAC assertions (a)(b): local run; no UAMI attached to host"
  echo "         These assertions MUST run in CI (uami-deploy-* federated) — TEST-001."
else
  acquire_token_as() {
    local client_id="$1"
    local resource="$2"
    az account get-access-token --resource "${resource}" --client-id "${client_id}" \
      --query accessToken -o tsv 2>/dev/null || true
  }

  assert_403() {
    local label="$1"
    local response_code="$2"
    if [[ "${response_code}" == "403" ]]; then
      ok "${label} → 403 as expected"
    else
      fail "${label} → got HTTP ${response_code} (expected 403)"
    fi
  }

  SEARCH_HOST="${SEARCH_ENDPOINT:-https://${SEARCH_SERVICE_NAME}.search.windows.net}"

  # (a) indexer attempts to CREATE an index — must be 403
  INDEXER_TOKEN="$(acquire_token_as "${UAMI_INDEXER_CLIENT_ID}" 'https://search.azure.com')"
  if [[ -n "${INDEXER_TOKEN}" ]]; then
    STATUS="$(curl -s -o /dev/null -w '%{http_code}' \
      -X PUT "${SEARCH_HOST}/indexes/__leastpriv_probe?api-version=2024-07-01" \
      -H "Authorization: Bearer ${INDEXER_TOKEN}" \
      -H 'Content-Type: application/json' \
      -d '{"name":"__leastpriv_probe","fields":[{"name":"id","type":"Edm.String","key":true}]}' || true)"
    assert_403 'uami-indexer-* index create' "${STATUS}"
  else
    fail 'uami-indexer-*: could not acquire AAD token for search.azure.com'
  fi

  # (b) agent attempts to WRITE a document to the index — must be 403
  AGENT_TOKEN="$(acquire_token_as "${UAMI_AGENT_CLIENT_ID}" 'https://search.azure.com')"
  if [[ -n "${AGENT_TOKEN}" ]]; then
    STATUS="$(curl -s -o /dev/null -w '%{http_code}' \
      -X POST "${SEARCH_HOST}/indexes/questions/docs/index?api-version=2024-07-01" \
      -H "Authorization: Bearer ${AGENT_TOKEN}" \
      -H 'Content-Type: application/json' \
      -d '{"value":[{"@search.action":"upload","id":"__leastpriv_probe"}]}' || true)"
    if [[ "${STATUS}" == "403" || "${STATUS}" == "404" ]]; then
      ok "uami-agent-* index write → ${STATUS} (403 expected; 404 = index not yet seeded)"
    else
      fail "uami-agent-* index write → HTTP ${STATUS} (expected 403, or 404 pre-seed)"
    fi
  else
    fail 'uami-agent-*: could not acquire AAD token for search.azure.com'
  fi
fi

# ----------------------------------------------------------------------------
section "AI Search synonym maps + 'questions' index — create-if-missing"
# ----------------------------------------------------------------------------
# The legacy `infra/scripts/create-questions-index.bicep` module is gated
# off (`deployAiSearchIndex=false`) because deploymentScripts fails on
# personal-MSA subscriptions. The ARM-native
# `Microsoft.Search/searchServices/indexes` resource returns opaque
# `BadRequest` on this schema (deferred follow-up). So we PUT both the
# three per-language synonym maps AND the index via REST here as the
# operator (`Search Service Contributor` granted by
# `rbac.bicep::deployerHumanSearchContributor`). Order matters: the index
# schema references the synonym maps by name, so they must exist first.
# Idempotent — PUT upserts.
if [[ -n "${SEARCH_SERVICE_NAME:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")/../scripts" && pwd)"
  SEARCH_HOST="https://${SEARCH_SERVICE_NAME}.search.windows.net"
  DEPLOYER_TOKEN="$(az account get-access-token --resource 'https://search.azure.com' --query accessToken -o tsv 2>/dev/null)"

  if [[ -z "${DEPLOYER_TOKEN}" ]]; then
    fail "could not acquire deployer token for search.azure.com"
  else
    # Synonym maps first. The on-disk JSON files use `synonyms` as an array
    # for readability; the Search REST API wants `synonyms` as a single
    # newline-joined string for the `solr` format. `jq` does the transform.
    for lang in en fr es; do
      SYN_PATH="${SCRIPT_DIR}/synonyms-${lang}.json"
      if [[ ! -f "${SYN_PATH}" ]]; then
        fail "synonyms-${lang}.json not found at ${SYN_PATH}"
        continue
      fi
      SYN_BODY="$(jq -c '. + {synonyms: (.synonyms | join("\n"))}' "${SYN_PATH}")"
      SYN_NAME="$(jq -r '.name' "${SYN_PATH}")"
      SYN_HTTP="$(curl -s -o /tmp/syn-${lang}-resp.json -w '%{http_code}' \
        -X PUT "${SEARCH_HOST}/synonymmaps/${SYN_NAME}?api-version=2024-07-01" \
        -H "Authorization: Bearer ${DEPLOYER_TOKEN}" \
        -H 'Content-Type: application/json' \
        --data "${SYN_BODY}" || true)"
      case "${SYN_HTTP}" in
        200|201|204) ok "PUT /synonymmaps/${SYN_NAME} → ${SYN_HTTP}" ;;
        *) fail "PUT /synonymmaps/${SYN_NAME} → ${SYN_HTTP}; body=$(head -c 200 /tmp/syn-${lang}-resp.json 2>/dev/null)" ;;
      esac
    done

    # Now the index — references the three maps by name.
    SCHEMA_PATH="${SCRIPT_DIR}/questions-index-schema.json"
    if [[ -f "${SCHEMA_PATH}" ]]; then
      INDEX_HTTP="$(curl -s -o /tmp/idx-resp.json -w '%{http_code}' \
        -X PUT "${SEARCH_HOST}/indexes/questions?api-version=2024-07-01" \
        -H "Authorization: Bearer ${DEPLOYER_TOKEN}" \
        -H 'Content-Type: application/json' \
        --data-binary @"${SCHEMA_PATH}" || true)"
      case "${INDEX_HTTP}" in
        200|201|204) ok "PUT /indexes/questions → ${INDEX_HTTP} (upserted)" ;;
        *) fail "PUT /indexes/questions → ${INDEX_HTTP}; body=$(head -c 300 /tmp/idx-resp.json 2>/dev/null)" ;;
      esac
    else
      fail "questions-index-schema.json not found at ${SCHEMA_PATH}"
    fi
  fi
fi

# ----------------------------------------------------------------------------
section "Summary: ${PASS_COUNT} OK · ${FAIL_COUNT} FAIL"
# ----------------------------------------------------------------------------
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  echo "$(color_fail 'post-provision hook FAILED') — see entries above"
  exit 1
fi
echo "$(color_ok 'post-provision hook PASSED')"
exit 0
