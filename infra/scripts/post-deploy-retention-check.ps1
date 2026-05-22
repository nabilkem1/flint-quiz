# Post-deploy retention assertions — PowerShell port of post-deploy-retention-check.sh.
#
# Reads retention values from AppConfig + the live resources and asserts:
#   1. Cosmos containers have TTL enabled.
#   2. audit retention > sessions retention (SEC-014).
#   3. Log Analytics retention matches policy.
#   4. audit-archive Blob container has a locked immutability policy.

$ErrorActionPreference = 'Continue'
$script:Fail = 0
$DbName = if ($env:COSMOS_DATABASE_NAME) { $env:COSMOS_DATABASE_NAME } else { 'flint-quiz' }

function Write-Ok  { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "OK  " -ForegroundColor Green -NoNewline; Write-Host "] $m" }
function Write-Bad { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "FAIL" -ForegroundColor Red   -NoNewline; Write-Host "] $m"; $script:Fail = 1 }

foreach ($v in @('AZURE_RESOURCE_GROUP','COSMOS_ACCOUNT_NAME','STORAGE_ACCOUNT_NAME','LOG_ANALYTICS_NAME','APP_CONFIG_NAME')) {
    if (-not [Environment]::GetEnvironmentVariable($v)) { Write-Bad "missing env $v" }
}
if ($script:Fail -ne 0) { exit 1 }

function Get-AppConfig { param([string]$Key)
    az appconfig kv show --name $env:APP_CONFIG_NAME --key $Key --query value -o tsv --auth-mode login 2>$null
}

Write-Host ''
Write-Host '== Read retention policy from AppConfig =='
$SessionsDays       = Get-AppConfig 'retention:sessionsScoredDays'
$AuditHotDays       = Get-AppConfig 'retention:auditHotDays'
$TranscriptDays     = Get-AppConfig 'retention:transcriptDays'
$LawHotDays         = Get-AppConfig 'retention:lawHotDays'
$AuditArchiveYears  = Get-AppConfig 'retention:auditArchiveYears'

$pairs = @(
    @{ Key='sessionsScoredDays'; Val=$SessionsDays },
    @{ Key='auditHotDays';        Val=$AuditHotDays },
    @{ Key='transcriptDays';      Val=$TranscriptDays },
    @{ Key='lawHotDays';          Val=$LawHotDays },
    @{ Key='auditArchiveYears';   Val=$AuditArchiveYears }
)
foreach ($p in $pairs) {
    if ($p.Val) { Write-Ok  "AppConfig retention:$($p.Key)=$($p.Val)" }
    else        { Write-Bad "AppConfig retention:$($p.Key) missing" }
}

Write-Host ''
Write-Host '== Cosmos: audit retention divergence (SEC-014) =='
if ($SessionsDays -and $AuditHotDays) {
    $sessionsInt = 0; $auditInt = 0
    if ([int]::TryParse($SessionsDays, [ref]$sessionsInt) -and [int]::TryParse($AuditHotDays, [ref]$auditInt)) {
        if ($auditInt -gt $sessionsInt) {
            Write-Ok  "audit retention (${auditInt}d) > session retention (${sessionsInt}d)"
        } else {
            Write-Bad "audit retention (${auditInt}d) MUST be strictly greater than session retention (${sessionsInt}d) — SEC-014"
        }
    } else {
        Write-Bad 'retention values not numeric — cannot compare'
    }
}

Write-Host ''
Write-Host '== Cosmos: containers have TTL enabled =='
foreach ($container in @('sessions','audit')) {
    $ttl = az cosmosdb sql container show `
        --account-name $env:COSMOS_ACCOUNT_NAME `
        --database-name $DbName `
        --name $container `
        --resource-group $env:AZURE_RESOURCE_GROUP `
        --query 'resource.defaultTtl' -o tsv 2>$null
    # defaultTtl=-1 → TTL on; positive int → TTL on; empty/null → off.
    if ($ttl -and ($ttl -eq '-1' -or $ttl -match '^[0-9]+$')) {
        Write-Ok  "container '$container': TTL enabled (defaultTtl=$ttl)"
    } else {
        Write-Bad "container '$container': TTL NOT enabled (defaultTtl='$ttl')"
    }
}

Write-Host ''
Write-Host '== Log Analytics retention matches policy =='
$lawDaysActual = az monitor log-analytics workspace show `
    -n $env:LOG_ANALYTICS_NAME -g $env:AZURE_RESOURCE_GROUP `
    --query retentionInDays -o tsv 2>$null
if ($LawHotDays -and $lawDaysActual) {
    if ($lawDaysActual -eq $LawHotDays) {
        Write-Ok  "LAW retention=${lawDaysActual}d matches policy"
    } else {
        Write-Bad "LAW retention=${lawDaysActual}d does not match policy (${LawHotDays}d)"
    }
}

Write-Host ''
Write-Host '== Blob audit-archive immutability =='
$ArchiveContainer = if ($env:AUDIT_ARCHIVE_CONTAINER) { $env:AUDIT_ARCHIVE_CONTAINER } else { 'audit-archive' }
$immutState = az storage container immutability-policy show `
    --account-name $env:STORAGE_ACCOUNT_NAME `
    --container-name $ArchiveContainer `
    --query 'state' -o tsv 2>$null
switch ($immutState) {
    'Locked'   { Write-Ok  'audit-archive immutability state = Locked' }
    'Unlocked' { Write-Bad 'audit-archive immutability state = Unlocked (must be locked before pre-public)' }
    default    { Write-Bad 'audit-archive: no immutability policy detected' }
}

Write-Host ''
if ($script:Fail -ne 0) {
    Write-Host 'post-deploy-retention-check FAILED' -ForegroundColor Red
    exit 1
}
Write-Host 'post-deploy-retention-check PASSED' -ForegroundColor Green
exit 0
