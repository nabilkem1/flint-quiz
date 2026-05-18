#!/usr/bin/env bash
# Pre-deploy checklist (TASK-202 / `specs/007-operational-runbook.md §8`).
#
# Runs BEFORE `azd up` provisions anything. Exits non-zero with a
# clear message when any precondition is missing. The hook is wired
# to `azure.yaml:hooks.preprovision`.
#
# Six checks, in deploy order:
#
#   1. Every Bicep module the main template references exists on disk.
#   2. `infra/main.parameters.<env>.json` carries every required key.
#   3. `azd env get-values` exposes the workload-tag values.
#   4. The deployer can resolve role assignments (`az role assignment list`).
#   5. AppConfig store endpoint is set (post first deploy it should be
#      readable; on a fresh deploy this is skipped).
#   6. Key Vault accessibility (post first deploy it must respond to
#      `az keyvault show`).
#
# A failure here is preferred to a half-broken `azd up`. The hook never
# asks for confirmation; it asserts and exits.

set -uo pipefail

FAIL=0

color_ok() { printf '\033[32m%s\033[0m' "$1"; }
color_fail() { printf '\033[31m%s\033[0m' "$1"; }
color_warn() { printf '\033[33m%s\033[0m' "$1"; }

ok()   { printf '  [%s] %s\n' "$(color_ok 'OK  ')" "$1"; }
bad()  { printf '  [%s] %s\n' "$(color_fail 'FAIL')" "$1"; FAIL=1; }
warn() { printf '  [%s] %s\n' "$(color_warn 'WARN')" "$1"; }
section() { printf '\n== %s ==\n' "$1"; }

# ----------------------------------------------------------------------------
section 'Required environment variables (azd populates these)'
# ----------------------------------------------------------------------------
for v in AZURE_ENV_NAME AZURE_LOCATION; do
  if [[ -n "${!v:-}" ]]; then
    ok "${v} = ${!v}"
  else
    bad "env ${v} is not set — run \`azd env new\` first"
  fi
done

ENV_NAME="${AZURE_ENV_NAME:-dev}"

# ----------------------------------------------------------------------------
section "Parameter file: infra/main.parameters.${ENV_NAME}.json"
# ----------------------------------------------------------------------------
PARAM_FILE="infra/main.parameters.${ENV_NAME}.json"
if [[ ! -f "${PARAM_FILE}" ]]; then
  # Fall back to the env-agnostic file. Acceptable for dev / new env.
  if [[ -f "infra/main.parameters.json" ]]; then
    ok "parameter file ${PARAM_FILE} not found — using fallback infra/main.parameters.json"
    PARAM_FILE='infra/main.parameters.json'
  else
    bad "neither ${PARAM_FILE} nor infra/main.parameters.json present"
    exit 1
  fi
else
  ok "parameter file ${PARAM_FILE} present"
fi

REQUIRED_PARAM_KEYS=(
  environmentName
  location
  prefix
  supportedLanguages
  modelDeploymentName
  modelName
  modelVersion
  cosmosSessionsTtlDays
  auditTtlDays
  voiceMaxSessionMinutes
  voiceIdleSeconds
  featuresApim
  tagOwner
  tagCostCenter
)

for key in "${REQUIRED_PARAM_KEYS[@]}"; do
  # Use `has(...)` not `-e`: a present param whose value is `false` or
  # `null` would otherwise be treated as missing (jq's `-e` returns
  # non-zero for falsy values).
  if jq -e ".parameters | has(\"${key}\")" "${PARAM_FILE}" >/dev/null 2>&1; then
    ok "param ${key} populated"
  else
    bad "param ${key} missing from ${PARAM_FILE}"
  fi
done

# ----------------------------------------------------------------------------
section 'Referenced Bicep modules exist on disk'
# ----------------------------------------------------------------------------
if [[ -f infra/main.bicep ]]; then
  # Extract module paths from the main template and verify each file exists.
  MODULES=$(grep -E "^\s*module\s+[A-Za-z0-9_]+\s+'[^']+'" infra/main.bicep \
            | sed -E "s/.*'([^']+)'.*/\1/" | sort -u)
  for module in ${MODULES}; do
    PATH_REL="infra/${module}"
    if [[ -f "${PATH_REL}" ]]; then
      ok "module ${module}"
    else
      bad "module ${module} referenced in main.bicep but not on disk"
    fi
  done
else
  bad "infra/main.bicep missing — cannot resolve module references"
fi

# ----------------------------------------------------------------------------
section 'Deployer can enumerate role assignments'
# ----------------------------------------------------------------------------
if command -v az >/dev/null 2>&1; then
  if az role assignment list --all --query '[0].id' -o tsv >/dev/null 2>&1; then
    ok "az role assignment list works (deployer has read access)"
  else
    bad "az role assignment list failed — pre-provision RBAC check would not run"
  fi
else
  warn "az CLI not installed — skipping role-assignment probe (CI MUST have it)"
fi

# ----------------------------------------------------------------------------
section "Repeat deploys: existing AppConfig + Key Vault probes (skipped on fresh)"
# ----------------------------------------------------------------------------
APP_CONFIG_NAME="${APP_CONFIG_NAME:-}"
KEY_VAULT_NAME="${KEY_VAULT_NAME:-}"
RG="${AZURE_RESOURCE_GROUP:-}"

if [[ -n "${APP_CONFIG_NAME}" && -n "${RG}" ]]; then
  if az appconfig show -n "${APP_CONFIG_NAME}" -g "${RG}" --query name -o tsv >/dev/null 2>&1; then
    # Probe a known key — `model:deploymentName`.
    if az appconfig kv show --name "${APP_CONFIG_NAME}" --key 'model:deploymentName' --auth-mode login --query value -o tsv >/dev/null 2>&1; then
      ok "AppConfig key 'model:deploymentName' readable"
    else
      warn "AppConfig key 'model:deploymentName' not present (expected on fresh deploys; seeded post-provision)"
    fi
  else
    warn "AppConfig ${APP_CONFIG_NAME} not yet provisioned (fresh deploy)"
  fi
fi

if [[ -n "${KEY_VAULT_NAME}" && -n "${RG}" ]]; then
  if az keyvault show -n "${KEY_VAULT_NAME}" -g "${RG}" --query name -o tsv >/dev/null 2>&1; then
    ok "Key Vault ${KEY_VAULT_NAME} reachable"
  else
    warn "Key Vault ${KEY_VAULT_NAME} not yet provisioned (fresh deploy)"
  fi
fi

# ----------------------------------------------------------------------------
section "Stale SERVICE_*_IMAGE_NAME guard (post-azd-down recovery)"
# ----------------------------------------------------------------------------
# `azd down` doesn't clear the per-service image refs azd persisted from a
# prior deploy. The next `azd up` then passes those (now-stale) tags into
# the bicep `*ImageRef` params via parameters.json, and ARM rejects the
# Container App / Job create with `MANIFEST_UNKNOWN` because the freshly
# rebuilt ACR has never carried those tags.
#
# Detection: probe the ACR for the persisted tag. If the ACR doesn't
# exist OR the tag is missing, clear the variable so the bootstrap
# (hello-world) fallback in main.parameters.json wins. The subsequent
# `azd deploy` rebuilds + repushes the real images.
clean_stale_image_var() {
  local var_name="$1"
  local var_val
  eval "var_val=\${$var_name:-}"
  [[ -z "${var_val}" ]] && return 0

  # Extract `<acr-host>/<repo>:<tag>` parts.
  local acr_host="${var_val%%/*}"
  local repo_and_tag="${var_val#*/}"
  local repo="${repo_and_tag%:*}"
  local tag="${repo_and_tag##*:}"
  local acr_name="${acr_host%%.*}"

  if az acr manifest show-tags -n "${acr_name}" --repository "${repo}" --query "[?name=='${tag}']" -o tsv 2>/dev/null | grep -q "${tag}"; then
    ok "${var_name} → ${tag} (manifest present in ${acr_name})"
  else
    warn "${var_name} → ${tag} not in ACR ${acr_name} — clearing so bootstrap fallback applies"
    azd env set "${var_name}" "" >/dev/null 2>&1 || true
    unset "${var_name}"
  fi
}

for var in SERVICE_QUIZ_AGENT_IMAGE_NAME SERVICE_SEED_LOADER_IMAGE_NAME SERVICE_SWEEPER_IMAGE_NAME; do
  clean_stale_image_var "${var}"
done

# ----------------------------------------------------------------------------
section "Environment guard: prevent prod-seed-in-dev mistakes"
# ----------------------------------------------------------------------------
# The seed loader is invoked from `infra/hooks/post-deploy-smoke.sh`.
# We assert the env matches the deploy target name so a wrong-env seed
# (e.g., running the prod seed loader against the dev RG) blows up
# loudly here, not silently mid-deploy.
EXPECTED_RG_PATTERN="^${prefix:-fq}-${ENV_NAME}-rg$"
if [[ -n "${RG}" ]]; then
  if [[ "${RG}" =~ ${EXPECTED_RG_PATTERN} ]]; then
    ok "AZURE_RESOURCE_GROUP=${RG} matches expected pattern for env=${ENV_NAME}"
  else
    bad "AZURE_RESOURCE_GROUP=${RG} does NOT match ${EXPECTED_RG_PATTERN} — wrong env?"
  fi
fi

printf '\n'
if [[ ${FAIL} -ne 0 ]]; then
  echo "$(color_fail 'pre-deploy hook FAILED') — fix the failures above before \`azd up\`."
  exit 1
fi
echo "$(color_ok 'pre-deploy hook PASSED')"
exit 0
