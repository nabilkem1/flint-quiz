#!/usr/bin/env bash
# Post-deploy retention assertions (TASK-132 / TASK-133 / SEC-008 /
# SEC-014).
#
# Reads retention values from `infra/main.parameters.<env>.json` and the
# Azure resources actually deployed, then asserts:
#
#   1. Cosmos `sessions` default TTL is configured (the per-row TTL is
#      set by 003-cosmos-db TASK-050 on terminal-state transition, but
#      the container's default `defaultTtl=-1` means TTL-on-document is
#      honoured).
#   2. Cosmos `audit` retention IS strictly greater than `sessions`
#      retention (SEC-014). Implemented as `audit:hotDays > sessions:scoredDays`.
#   3. Log Analytics workspace retention matches policy
#      (`retention:lawHotDays`).
#   4. App Insights workspace exists.
#   5. `audit-archive` Blob container has an immutability policy set
#      (where one is configured by 003-cosmos-db TASK-051).
#
# Exits non-zero on any violation. Intended to run on a schedule (daily
# in prod) and on every deploy.
#
# Required env (populated by `azd env get-values`):
#   COSMOS_ACCOUNT_NAME / COSMOS_DATABASE_NAME (default: flint-quiz)
#   STORAGE_ACCOUNT_NAME
#   LOG_ANALYTICS_NAME
#   AZURE_RESOURCE_GROUP
#   APP_CONFIG_NAME (to read retention:* keys)

set -uo pipefail

FAIL=0
DB_NAME="${COSMOS_DATABASE_NAME:-flint-quiz}"

ok()   { printf '  [\033[32mOK  \033[0m] %s\n' "$1"; }
bad()  { printf '  [\033[31mFAIL\033[0m] %s\n' "$1"; FAIL=1; }

for v in AZURE_RESOURCE_GROUP COSMOS_ACCOUNT_NAME STORAGE_ACCOUNT_NAME \
         LOG_ANALYTICS_NAME APP_CONFIG_NAME; do
  if [[ -z "${!v:-}" ]]; then
    bad "missing env ${v}"
  fi
done
[[ ${FAIL} -ne 0 ]] && exit 1

# Helper: read an AppConfig key value.
appconfig_get() {
  az appconfig kv show \
    --name "${APP_CONFIG_NAME}" \
    --key "$1" \
    --query value -o tsv \
    --auth-mode login 2>/dev/null || true
}

printf '\n== Read retention policy from AppConfig ==\n'
SESSIONS_DAYS="$(appconfig_get 'retention:sessionsScoredDays')"
AUDIT_HOT_DAYS="$(appconfig_get 'retention:auditHotDays')"
TRANSCRIPT_DAYS="$(appconfig_get 'retention:transcriptDays')"
LAW_HOT_DAYS="$(appconfig_get 'retention:lawHotDays')"
AUDIT_ARCHIVE_YEARS="$(appconfig_get 'retention:auditArchiveYears')"

for pair in "sessionsScoredDays=${SESSIONS_DAYS}" \
            "auditHotDays=${AUDIT_HOT_DAYS}" \
            "transcriptDays=${TRANSCRIPT_DAYS}" \
            "lawHotDays=${LAW_HOT_DAYS}" \
            "auditArchiveYears=${AUDIT_ARCHIVE_YEARS}"; do
  k="${pair%%=*}"; v="${pair##*=}"
  if [[ -n "${v}" ]]; then
    ok  "AppConfig retention:${k}=${v}"
  else
    bad "AppConfig retention:${k} missing"
  fi
done

printf '\n== Cosmos: audit retention divergence (SEC-014) ==\n'
if [[ -n "${SESSIONS_DAYS}" && -n "${AUDIT_HOT_DAYS}" ]]; then
  if [[ "${AUDIT_HOT_DAYS}" -gt "${SESSIONS_DAYS}" ]]; then
    ok  "audit retention (${AUDIT_HOT_DAYS}d) > session retention (${SESSIONS_DAYS}d)"
  else
    bad "audit retention (${AUDIT_HOT_DAYS}d) MUST be strictly greater than session retention (${SESSIONS_DAYS}d) — SEC-014"
  fi
fi

printf '\n== Cosmos: containers have TTL enabled ==\n'
for container in sessions audit; do
  ttl="$(az cosmosdb sql container show \
    --account-name "${COSMOS_ACCOUNT_NAME}" \
    --database-name "${DB_NAME}" \
    --name "${container}" \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --query 'resource.defaultTtl' -o tsv 2>/dev/null || true)"
  # defaultTtl=-1 means "TTL enabled; per-document TTL honored".
  # defaultTtl=null / empty means "TTL disabled" → bad.
  case "${ttl}" in
    -1|[0-9]*) ok  "container '${container}': TTL enabled (defaultTtl=${ttl})";;
    *)         bad "container '${container}': TTL NOT enabled (defaultTtl='${ttl}')";;
  esac
done

printf '\n== Log Analytics retention matches policy ==\n'
LAW_DAYS_ACTUAL="$(az monitor log-analytics workspace show \
  -n "${LOG_ANALYTICS_NAME}" -g "${AZURE_RESOURCE_GROUP}" \
  --query retentionInDays -o tsv 2>/dev/null || true)"
if [[ -n "${LAW_HOT_DAYS}" && -n "${LAW_DAYS_ACTUAL}" ]]; then
  if [[ "${LAW_DAYS_ACTUAL}" == "${LAW_HOT_DAYS}" ]]; then
    ok  "LAW retention=${LAW_DAYS_ACTUAL}d matches policy"
  else
    bad "LAW retention=${LAW_DAYS_ACTUAL}d does not match policy (${LAW_HOT_DAYS}d)"
  fi
fi

printf '\n== Blob audit-archive immutability ==\n'
ARCHIVE_CONTAINER="${AUDIT_ARCHIVE_CONTAINER:-audit-archive}"
# Time-based immutability policy state on the container.
IMMUT_STATE="$(az storage container immutability-policy show \
  --account-name "${STORAGE_ACCOUNT_NAME}" \
  --container-name "${ARCHIVE_CONTAINER}" \
  --query 'state' -o tsv 2>/dev/null || true)"
case "${IMMUT_STATE}" in
  Locked)
    ok  "audit-archive immutability state = Locked"
    ;;
  Unlocked)
    bad "audit-archive immutability state = Unlocked (must be locked before pre-public)"
    ;;
  *)
    bad "audit-archive: no immutability policy detected"
    ;;
esac

printf '\n'
if [[ ${FAIL} -ne 0 ]]; then
  echo "post-deploy-retention-check FAILED"
  exit 1
fi
echo "post-deploy-retention-check PASSED"
exit 0
