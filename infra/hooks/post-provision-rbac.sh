#!/usr/bin/env bash
# Post-provision RBAC scope verification (TASK-121 / SEC-005).
#
# Enumerates role assignments per runtime UAMI and asserts:
#
#   1. No assignment is at subscription scope on a runtime UAMI
#      (uami-agent-*, uami-indexer-*).
#   2. No runtime UAMI holds Owner / Contributor / User Access
#      Administrator.
#   3. `Search Index Data Contributor` lands only on uami-indexer-*.
#   4. `Search Service Contributor` lands only on uami-deploy-* (CI
#      principal).
#
# Exits non-zero on any violation so `azd up` surfaces it. Intended to
# run alongside `infra/hooks/post-provision.sh` (which already covers
# resource health probes + privileged-role negatives). This script is
# the **scope-specific** complement called out in the 007-security
# prompt's TASK-121.
#
# Required env (populated by `azd env get-values`):
#   UAMI_AGENT_CLIENT_ID
#   UAMI_INDEXER_CLIENT_ID
#   UAMI_DEPLOY_CLIENT_ID

set -uo pipefail

FAIL=0

color_ok()   { printf '\033[32m%s\033[0m' "$1"; }
color_fail() { printf '\033[31m%s\033[0m' "$1"; }

ok()    { printf '  [%s] %s\n' "$(color_ok 'OK  ')" "$1"; }
bad()   { printf '  [%s] %s\n' "$(color_fail 'FAIL')" "$1"; FAIL=1; }

# Roles a runtime UAMI must NEVER hold. The agent-side hook already
# checks these; we re-assert here so the RBAC-scope script is the
# single source of truth callers can wire into a different stage.
PRIV_ROLES='Owner|Contributor|User Access Administrator'

# Roles whose assignment we want to constrain to a specific UAMI.
SEARCH_INDEX_DATA_CONTRIBUTOR='Search Index Data Contributor'
SEARCH_SERVICE_CONTRIBUTOR='Search Service Contributor'

resolve_sp() {
  # Returns the service principal object ID for a UAMI client ID.
  az ad sp show --id "$1" --query id -o tsv 2>/dev/null || true
}

list_assignments() {
  # Returns a JSON array of role assignments for the given SP OID
  # across every scope it touches.
  local sp_oid="$1"
  az role assignment list --assignee-object-id "${sp_oid}" \
    --assignee-principal-type ServicePrincipal --all -o json 2>/dev/null \
    || echo '[]'
}

assert_no_subscription_scope() {
  local label="$1" assignments_json="$2"
  if echo "${assignments_json}" | jq -e '.[] | select(.scope | test("^/subscriptions/[^/]+$"))' >/dev/null 2>&1; then
    bad "${label}: holds at least one subscription-scoped role assignment"
  else
    ok  "${label}: no subscription-scoped role assignments"
  fi
}

assert_no_priv_roles() {
  local label="$1" assignments_json="$2"
  if echo "${assignments_json}" | jq -er ".[] | select(.roleDefinitionName | test(\"^(${PRIV_ROLES})$\"))" >/dev/null 2>&1; then
    bad "${label}: holds at least one of ${PRIV_ROLES}"
  else
    ok  "${label}: no Owner/Contributor/UAA"
  fi
}

assert_only_assignee_has_role() {
  # Asserts that within `assignments_json`, the role appears only when the
  # principal IS the expected one. Useful for "X role belongs ONLY on
  # uami-Y" rules.
  local label="$1" role="$2" assignments_json="$3" allowed_sp_oid="$4"
  local bad_hits
  bad_hits="$(echo "${assignments_json}" | jq -r --arg r "${role}" --arg keep "${allowed_sp_oid}" \
    '.[] | select(.roleDefinitionName == $r and .principalId != $keep) | .principalId' 2>/dev/null || true)"
  if [[ -n "${bad_hits}" ]]; then
    bad "${label}: role '${role}' assigned to principal(s) outside the allowlist: ${bad_hits}"
  else
    ok  "${label}: role '${role}' scoped only to the allowed UAMI"
  fi
}

main() {
  for v in UAMI_AGENT_CLIENT_ID UAMI_INDEXER_CLIENT_ID UAMI_DEPLOY_CLIENT_ID; do
    if [[ -z "${!v:-}" ]]; then
      bad "missing env ${v}"
    fi
  done
  [[ ${FAIL} -ne 0 ]] && { echo "post-provision-rbac: required env missing — aborting."; exit 1; }

  printf '\n== Resolve UAMI service principals ==\n'
  AGENT_SP="$(resolve_sp "${UAMI_AGENT_CLIENT_ID}")"
  INDEXER_SP="$(resolve_sp "${UAMI_INDEXER_CLIENT_ID}")"
  DEPLOY_SP="$(resolve_sp "${UAMI_DEPLOY_CLIENT_ID}")"
  for pair in "uami-agent-*:${AGENT_SP}" "uami-indexer-*:${INDEXER_SP}" "uami-deploy-*:${DEPLOY_SP}"; do
    label="${pair%%:*}"; sp="${pair##*:}"
    if [[ -n "${sp}" ]]; then ok "${label}: resolved (${sp:0:8}...)"
    else bad "${label}: could not resolve service principal"; fi
  done

  printf '\n== Runtime UAMI: no privileged roles, no subscription scope ==\n'
  AGENT_ROLES="$(list_assignments "${AGENT_SP}")"
  INDEXER_ROLES="$(list_assignments "${INDEXER_SP}")"
  assert_no_priv_roles 'uami-agent-*'    "${AGENT_ROLES}"
  assert_no_subscription_scope 'uami-agent-*' "${AGENT_ROLES}"
  assert_no_priv_roles 'uami-indexer-*'  "${INDEXER_ROLES}"
  assert_no_subscription_scope 'uami-indexer-*' "${INDEXER_ROLES}"

  printf '\n== Search role scoping (least-privilege) ==\n'
  # Combine all assignments across the three SPs to evaluate "only X
  # principal holds role R".
  ALL_ROLES_JSON="$(jq -s 'add' \
    <(echo "${AGENT_ROLES}") \
    <(echo "${INDEXER_ROLES}") \
    <(list_assignments "${DEPLOY_SP}"))"
  assert_only_assignee_has_role \
    'Search Index Data Contributor' "${SEARCH_INDEX_DATA_CONTRIBUTOR}" \
    "${ALL_ROLES_JSON}" "${INDEXER_SP}"
  assert_only_assignee_has_role \
    'Search Service Contributor'    "${SEARCH_SERVICE_CONTRIBUTOR}" \
    "${ALL_ROLES_JSON}" "${DEPLOY_SP}"

  printf '\n'
  if [[ ${FAIL} -ne 0 ]]; then
    echo "$(color_fail 'post-provision-rbac FAILED') — fix the RBAC; do not weaken the check."
    exit 1
  fi
  echo "$(color_ok 'post-provision-rbac PASSED')"
  exit 0
}

main "$@"
