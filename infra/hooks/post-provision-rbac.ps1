# Post-provision RBAC scope verification — PowerShell port of post-provision-rbac.sh.
#
# Asserts:
#   1. No assignment is at subscription scope on a runtime UAMI.
#   2. No runtime UAMI holds Owner / Contributor / User Access Administrator.
#   3. 'Search Index Data Contributor' lives only on uami-indexer-*.
#   4. 'Search Service Contributor' lives only on uami-deploy-*.

$ErrorActionPreference = 'Continue'
$script:Fail = 0

function Write-Ok  { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "OK  " -ForegroundColor Green -NoNewline; Write-Host "] $m" }
function Write-Bad { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "FAIL" -ForegroundColor Red   -NoNewline; Write-Host "] $m"; $script:Fail++ }

$PrivRolesPattern               = '^(Owner|Contributor|User Access Administrator)$'
$SearchIndexDataContributor     = 'Search Index Data Contributor'
$SearchServiceContributor       = 'Search Service Contributor'

function Resolve-SP { param([string]$ClientId)
    az ad sp show --id $ClientId --query id -o tsv 2>$null
}

function Get-Assignments { param([string]$SpOid)
    $json = az role assignment list --assignee-object-id $SpOid `
        --assignee-principal-type ServicePrincipal --all -o json 2>$null
    if (-not $json) { return @() }
    try { return @($json | ConvertFrom-Json) } catch { return @() }
}

function Assert-NoSubscriptionScope { param([string]$Label, [array]$Assignments)
    if ($Assignments | Where-Object { $_.scope -match '^/subscriptions/[^/]+$' }) {
        Write-Bad "${Label}: holds at least one subscription-scoped role assignment"
    } else {
        Write-Ok  "${Label}: no subscription-scoped role assignments"
    }
}

function Assert-NoPrivRoles { param([string]$Label, [array]$Assignments)
    if ($Assignments | Where-Object { $_.roleDefinitionName -match $PrivRolesPattern }) {
        Write-Bad "${Label}: holds at least one of Owner|Contributor|User Access Administrator"
    } else {
        Write-Ok  "${Label}: no Owner/Contributor/UAA"
    }
}

function Assert-OnlyAssigneeHasRole {
    param([string]$Label, [string]$Role, [array]$Assignments, [string]$AllowedSpOid)
    $badHits = $Assignments |
        Where-Object { $_.roleDefinitionName -eq $Role -and $_.principalId -ne $AllowedSpOid } |
        ForEach-Object { $_.principalId }
    if ($badHits) {
        Write-Bad "${Label}: role '$Role' assigned to principal(s) outside the allowlist: $($badHits -join ', ')"
    } else {
        Write-Ok  "${Label}: role '$Role' scoped only to the allowed UAMI"
    }
}

foreach ($v in @('UAMI_AGENT_CLIENT_ID','UAMI_INDEXER_CLIENT_ID','UAMI_DEPLOY_CLIENT_ID')) {
    if (-not [Environment]::GetEnvironmentVariable($v)) { Write-Bad "missing env $v" }
}
if ($script:Fail -ne 0) { Write-Host 'post-provision-rbac: required env missing — aborting.'; exit 1 }

Write-Host ''
Write-Host '== Resolve UAMI service principals =='
$AgentSp   = Resolve-SP $env:UAMI_AGENT_CLIENT_ID
$IndexerSp = Resolve-SP $env:UAMI_INDEXER_CLIENT_ID
$DeploySp  = Resolve-SP $env:UAMI_DEPLOY_CLIENT_ID
foreach ($pair in @(
    @{ Label='uami-agent-*';   Sp=$AgentSp },
    @{ Label='uami-indexer-*'; Sp=$IndexerSp },
    @{ Label='uami-deploy-*';  Sp=$DeploySp }
)) {
    if ($pair.Sp) { Write-Ok  "$($pair.Label): resolved ($($pair.Sp.Substring(0,8))...)" }
    else          { Write-Bad "$($pair.Label): could not resolve service principal" }
}

Write-Host ''
Write-Host '== Runtime UAMI: no privileged roles, no subscription scope =='
$AgentRoles   = Get-Assignments $AgentSp
$IndexerRoles = Get-Assignments $IndexerSp
Assert-NoPrivRoles         'uami-agent-*'   $AgentRoles
Assert-NoSubscriptionScope 'uami-agent-*'   $AgentRoles
Assert-NoPrivRoles         'uami-indexer-*' $IndexerRoles
Assert-NoSubscriptionScope 'uami-indexer-*' $IndexerRoles

Write-Host ''
Write-Host '== Search role scoping (least-privilege) =='
$DeployRoles = Get-Assignments $DeploySp
$AllRoles    = @($AgentRoles + $IndexerRoles + $DeployRoles)
Assert-OnlyAssigneeHasRole 'Search Index Data Contributor' $SearchIndexDataContributor $AllRoles $IndexerSp
Assert-OnlyAssigneeHasRole 'Search Service Contributor'    $SearchServiceContributor    $AllRoles $DeploySp

Write-Host ''
if ($script:Fail -ne 0) {
    Write-Host 'post-provision-rbac FAILED' -ForegroundColor Red -NoNewline
    Write-Host ' — fix the RBAC; do not weaken the check.'
    exit 1
}
Write-Host 'post-provision-rbac PASSED' -ForegroundColor Green
exit 0
