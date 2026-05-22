# Flint Quiz — post-provision validation hook (PowerShell port of post-provision.sh).
#
# Three responsibilities, identical to the bash original:
#   (1) Resource health probes (az ... show per resource).
#   (2) Positive posture assertions (disableLocalAuth, RBAC mode, etc.).
#   (3) Negative RBAC assertions (least privilege).
#
# Plus the create-if-missing synonym maps + 'questions' index PUTs.

$ErrorActionPreference = 'Continue'
$script:Pass = 0
$script:Fail = 0

function Write-Ok      { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "OK  " -ForegroundColor Green -NoNewline; Write-Host "] $m"; $script:Pass++ }
function Write-Bad     { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "FAIL" -ForegroundColor Red   -NoNewline; Write-Host "] $m"; $script:Fail++ }
function Write-Warn    { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "WARN" -ForegroundColor Yellow -NoNewline; Write-Host "] $m" }
function Write-Skip    { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "SKIP" -ForegroundColor Yellow -NoNewline; Write-Host "] $m" }
function Write-Section { param([string]$m) Write-Host ""; Write-Host "== $m ==" }

function Get-EnvVar { param([string]$Name) [Environment]::GetEnvironmentVariable($Name) }

# ---------------------------------------------------------------------------
Write-Section 'Required environment values'
# ---------------------------------------------------------------------------
$requiredEnv = @(
    'AZURE_RESOURCE_GROUP','AZURE_LOCATION','AZURE_SUBSCRIPTION_ID',
    'APP_CONFIG_ENDPOINT','KEY_VAULT_NAME','COSMOS_ACCOUNT_NAME',
    'SEARCH_SERVICE_NAME','STORAGE_ACCOUNT_NAME','FOUNDRY_ACCOUNT_NAME',
    'FOUNDRY_PROJECT_NAME','MODEL_DEPLOYMENT_NAME',
    'UAMI_AGENT_CLIENT_ID','UAMI_INDEXER_CLIENT_ID','UAMI_DEPLOY_CLIENT_ID'
)
foreach ($v in $requiredEnv) {
    if (Get-EnvVar $v) { Write-Ok "env $v present" } else { Write-Bad "env $v missing" }
}

$Rg                  = Get-EnvVar 'AZURE_RESOURCE_GROUP'
$KeyVaultName        = Get-EnvVar 'KEY_VAULT_NAME'
$AppConfigName       = Get-EnvVar 'APP_CONFIG_NAME'
$StorageAccountName  = Get-EnvVar 'STORAGE_ACCOUNT_NAME'
$CosmosAccountName   = Get-EnvVar 'COSMOS_ACCOUNT_NAME'
$SearchServiceName   = Get-EnvVar 'SEARCH_SERVICE_NAME'
$AppInsightsName     = Get-EnvVar 'APP_INSIGHTS_NAME'
$LogAnalyticsName    = Get-EnvVar 'LOG_ANALYTICS_NAME'
$FoundryAccountName  = Get-EnvVar 'FOUNDRY_ACCOUNT_NAME'
$FoundryProjectName  = Get-EnvVar 'FOUNDRY_PROJECT_NAME'
$ModelDeploymentName = Get-EnvVar 'MODEL_DEPLOYMENT_NAME'
$SearchEndpoint      = Get-EnvVar 'SEARCH_ENDPOINT'
$UamiAgentClientId   = Get-EnvVar 'UAMI_AGENT_CLIENT_ID'
$UamiIndexerClientId = Get-EnvVar 'UAMI_INDEXER_CLIENT_ID'
$UamiDeployClientId  = Get-EnvVar 'UAMI_DEPLOY_CLIENT_ID'

# ---------------------------------------------------------------------------
Write-Section 'Resource health probes'
# ---------------------------------------------------------------------------
function Invoke-Probe {
    param([string]$Label, [scriptblock]$Action)
    $null = & $Action 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Ok $Label } else { Write-Bad $Label }
}

Invoke-Probe 'Resource group'       { az group show -n $Rg }
Invoke-Probe 'Key Vault'            { az keyvault show -n $KeyVaultName -g $Rg }
Invoke-Probe 'App Configuration'    { az appconfig show -n $AppConfigName -g $Rg }
Invoke-Probe 'Storage account'      { az storage account show -n $StorageAccountName -g $Rg }
Invoke-Probe 'Cosmos account'       { az cosmosdb show -n $CosmosAccountName -g $Rg }
Invoke-Probe 'AI Search service'    { az search service show -n $SearchServiceName -g $Rg }
Invoke-Probe 'Application Insights' { az monitor app-insights component show --app $AppInsightsName -g $Rg }
Invoke-Probe 'Log Analytics'        { az monitor log-analytics workspace show -n $LogAnalyticsName -g $Rg }
Invoke-Probe 'Foundry account'      { az cognitiveservices account show -n $FoundryAccountName -g $Rg }

# Foundry project (child of the account); no dedicated CLI yet — use generic resource list.
$count = az resource list -g $Rg `
    --resource-type 'Microsoft.CognitiveServices/accounts/projects' `
    --query "[?name=='$FoundryAccountName/$FoundryProjectName'] | length(@)" -o tsv 2>$null
if ($LASTEXITCODE -eq 0 -and $count -eq '1') {
    Write-Ok 'Foundry project'
} else {
    Write-Bad 'Foundry project'
}

Invoke-Probe 'Model deployment' {
    az cognitiveservices account deployment show -n $FoundryAccountName -g $Rg --deployment-name $ModelDeploymentName
}

# ---------------------------------------------------------------------------
Write-Section 'Posture assertions (positive)'
# ---------------------------------------------------------------------------
function Test-True  { param([string]$v) ($v -ne $null) -and ($v.ToString().ToLower() -eq 'true') }
function Test-False { param([string]$v) ($v -ne $null) -and ($v.ToString().ToLower() -eq 'false') }

if (Test-True (az cosmosdb show -n $CosmosAccountName -g $Rg --query disableLocalAuth -o tsv 2>$null)) {
    Write-Ok 'Cosmos disableLocalAuth=true (SEC-004)'
} else { Write-Bad 'Cosmos disableLocalAuth is NOT true — SEC-004 violation' }

if (Test-True (az search service show -n $SearchServiceName -g $Rg --query disableLocalAuth -o tsv 2>$null)) {
    Write-Ok 'AI Search disableLocalAuth=true'
} else { Write-Bad 'AI Search disableLocalAuth is NOT true' }

if (Test-True (az appconfig show -n $AppConfigName -g $Rg --query disableLocalAuth -o tsv 2>$null)) {
    Write-Ok 'App Configuration disableLocalAuth=true'
} else { Write-Bad 'App Configuration disableLocalAuth is NOT true' }

if (Test-False (az storage account show -n $StorageAccountName -g $Rg --query allowSharedKeyAccess -o tsv 2>$null)) {
    Write-Ok 'Storage allowSharedKeyAccess=false'
} else { Write-Bad 'Storage allowSharedKeyAccess is NOT false' }

if (Test-True (az cognitiveservices account show -n $FoundryAccountName -g $Rg --query properties.disableLocalAuth -o tsv 2>$null)) {
    Write-Ok 'Foundry account disableLocalAuth=true (Entra-only)'
} else { Write-Bad 'Foundry account disableLocalAuth is NOT true' }

# Key Vault: RBAC mode + purge protection.
$kvJson = az keyvault show -n $KeyVaultName -g $Rg -o json 2>$null
if ($kvJson) {
    try {
        $kv = $kvJson | ConvertFrom-Json
        if ($kv.properties.enableRbacAuthorization) { Write-Ok 'Key Vault enableRbacAuthorization=true (RBAC mode, SEC-013)' }
        else { Write-Bad 'Key Vault is NOT in RBAC mode' }
        if ($kv.properties.enablePurgeProtection) { Write-Ok 'Key Vault purge protection ON (SEC-013)' }
        else { Write-Bad 'Key Vault purge protection is NOT enabled' }
    } catch {
        Write-Bad "Key Vault JSON parse error: $_"
    }
} else {
    Write-Bad 'Key Vault details unavailable'
}

# ---------------------------------------------------------------------------
Write-Section 'Posture assertions (negative — least privilege)'
# ---------------------------------------------------------------------------
$PrivRolesPattern = '^(Owner|Contributor|User Access Administrator)$'

function Test-NoPrivilegedRoles {
    param([string]$ClientId, [string]$Label)
    $spOid = az ad sp show --id $ClientId --query id -o tsv 2>$null
    if (-not $spOid) {
        Write-Bad "${Label}: cannot resolve service principal for clientId=$ClientId"
        return
    }
    $roles = az role assignment list --assignee-object-id $spOid `
        --assignee-principal-type ServicePrincipal --all `
        --query '[].roleDefinitionName' -o tsv 2>$null
    if ($roles -and ($roles -split "`n" | Where-Object { $_ -match $PrivRolesPattern })) {
        Write-Bad "${Label}: holds at least one of Owner/Contributor/UAA — escalation risk"
    } else {
        Write-Ok "${Label}: no Owner/Contributor/User Access Administrator anywhere"
    }
}

Test-NoPrivilegedRoles -ClientId $UamiAgentClientId   -Label 'uami-agent-*'
Test-NoPrivilegedRoles -ClientId $UamiIndexerClientId -Label 'uami-indexer-*'

# uami-deploy-* is allowed Contributor on the env RG — only verify NOT Owner/UAA and not subscription-scoped.
$deploySp = az ad sp show --id $UamiDeployClientId --query id -o tsv 2>$null
if ($deploySp) {
    $deployRolesJson = az role assignment list --assignee-object-id $deploySp `
        --assignee-principal-type ServicePrincipal --all -o json 2>$null
    if (-not $deployRolesJson) { $deployRolesJson = '[]' }
    try {
        $deployRoles = $deployRolesJson | ConvertFrom-Json
        if ($deployRoles | Where-Object { $_.roleDefinitionName -in @('Owner','User Access Administrator') }) {
            Write-Bad 'uami-deploy-*: holds Owner or User Access Administrator (forbidden)'
        } else {
            Write-Ok 'uami-deploy-*: no Owner / UAA assignments'
        }
        if ($deployRoles | Where-Object { $_.scope -match '^/subscriptions/[^/]+$' }) {
            Write-Bad 'uami-deploy-*: holds subscription-scoped assignment (env-RG-scope only)'
        } else {
            Write-Ok 'uami-deploy-*: no subscription-scoped role assignments'
        }
    } catch {
        Write-Bad "uami-deploy-*: role JSON parse error: $_"
    }
}

# ---- 403 assertions: indexer cannot create / agent cannot write -----------
# Only run when a UAMI is actually attached to the host (Azure VM, ACI, ACA, etc.).
$IsAzureHost = $false
if ((Get-EnvVar 'IDENTITY_ENDPOINT') -or (Get-EnvVar 'MSI_ENDPOINT')) {
    $IsAzureHost = $true
} else {
    try {
        $imdsResp = Invoke-WebRequest -Uri 'http://169.254.169.254/metadata/instance?api-version=2021-02-01' `
            -Headers @{ Metadata = 'true' } -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($imdsResp.StatusCode -eq 200) { $IsAzureHost = $true }
    } catch {}
}

if (-not $IsAzureHost) {
    Write-Skip 'negative RBAC assertions (a)(b): local run; no UAMI attached to host'
    Write-Host "         These assertions MUST run in CI (uami-deploy-* federated) — TEST-001."
} else {
    function Get-TokenAs {
        param([string]$ClientId, [string]$Resource)
        az account get-access-token --resource $Resource --client-id $ClientId --query accessToken -o tsv 2>$null
    }

    function Assert-403 {
        param([string]$Label, [string]$Code)
        if ($Code -eq '403') { Write-Ok "$Label → 403 as expected" }
        else                 { Write-Bad "$Label → got HTTP $Code (expected 403)" }
    }

    $SearchHost = if ($SearchEndpoint) { $SearchEndpoint } else { "https://$SearchServiceName.search.windows.net" }

    # (a) indexer attempts to CREATE an index — must be 403.
    # If token acquisition fails (no UAMI attached, IMDS lied), SKIP rather
    # than FAIL — the bash hook does the same on dev hosts and this assertion
    # only carries real signal on a runtime host where the UAMI is bound.
    $indexerToken = Get-TokenAs -ClientId $UamiIndexerClientId -Resource 'https://search.azure.com'
    if ($indexerToken) {
        try {
            $resp = Invoke-WebRequest -Uri "$SearchHost/indexes/__leastpriv_probe?api-version=2024-07-01" `
                -Method PUT `
                -Headers @{ Authorization = "Bearer $indexerToken"; 'Content-Type' = 'application/json' } `
                -Body '{"name":"__leastpriv_probe","fields":[{"name":"id","type":"Edm.String","key":true}]}' `
                -UseBasicParsing -ErrorAction Stop
            Assert-403 'uami-indexer-* index create' ([string]$resp.StatusCode)
        } catch {
            $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
            Assert-403 'uami-indexer-* index create' ([string]$code)
        }
    } else {
        Write-Skip 'uami-indexer-* AAD token unavailable on this host — negative assertion (a) deferred to CI'
    }

    # (b) agent attempts to WRITE a document — must be 403.
    $agentToken = Get-TokenAs -ClientId $UamiAgentClientId -Resource 'https://search.azure.com'
    if ($agentToken) {
        $code = 0
        try {
            $resp = Invoke-WebRequest -Uri "$SearchHost/indexes/questions/docs/index?api-version=2024-07-01" `
                -Method POST `
                -Headers @{ Authorization = "Bearer $agentToken"; 'Content-Type' = 'application/json' } `
                -Body '{"value":[{"@search.action":"upload","id":"__leastpriv_probe"}]}' `
                -UseBasicParsing -ErrorAction Stop
            $code = [int]$resp.StatusCode
        } catch {
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        }
        if ($code -eq 403 -or $code -eq 404) {
            Write-Ok "uami-agent-* index write → $code (403 expected; 404 = index not yet seeded)"
        } else {
            Write-Bad "uami-agent-* index write → HTTP $code (expected 403, or 404 pre-seed)"
        }
    } else {
        Write-Skip 'uami-agent-* AAD token unavailable on this host — negative assertion (b) deferred to CI'
    }
}

# ---------------------------------------------------------------------------
Write-Section "AI Search synonym maps + 'questions' index — create-if-missing"
# ---------------------------------------------------------------------------
if ($SearchServiceName) {
    $ScriptDir   = Resolve-Path (Join-Path (Split-Path -Parent $PSCommandPath) '..\scripts')
    $SearchHost  = "https://$SearchServiceName.search.windows.net"
    $deployerToken = az account get-access-token --resource 'https://search.azure.com' --query accessToken -o tsv 2>$null

    if (-not $deployerToken) {
        Write-Bad 'could not acquire deployer token for search.azure.com'
    } else {
        $authHeaders = @{
            Authorization  = "Bearer $deployerToken"
            'Content-Type' = 'application/json'
        }

        # Synonym maps first — array → newline-joined string for solr format.
        foreach ($lang in @('en','fr','es')) {
            $synPath = Join-Path $ScriptDir "synonyms-$lang.json"
            if (-not (Test-Path $synPath)) {
                Write-Bad "synonyms-$lang.json not found at $synPath"
                continue
            }
            try {
                $synObj = Get-Content -Raw $synPath | ConvertFrom-Json
                $synObj.synonyms = ($synObj.synonyms -join "`n")
                $synBody = $synObj | ConvertTo-Json -Depth 10 -Compress
                $synName = $synObj.name
                $code = 0
                $body = ''
                try {
                    $resp = Invoke-WebRequest -Uri "$SearchHost/synonymmaps/$synName`?api-version=2024-07-01" `
                        -Method PUT -Headers $authHeaders -Body $synBody -UseBasicParsing -ErrorAction Stop
                    $code = [int]$resp.StatusCode
                } catch {
                    if ($_.Exception.Response) {
                        $code = [int]$_.Exception.Response.StatusCode
                        try { $body = $_.ErrorDetails.Message } catch {}
                    }
                }
                if ($code -in 200,201,204) {
                    Write-Ok "PUT /synonymmaps/$synName → $code"
                } else {
                    $snippet = if ($body) { $body.Substring(0, [Math]::Min(200, $body.Length)) } else { '' }
                    Write-Bad "PUT /synonymmaps/$synName → $code; body=$snippet"
                }
            } catch {
                Write-Bad "PUT /synonymmaps for $lang failed: $_"
            }
        }

        # Index — references the three synonym maps by name.
        $schemaPath = Join-Path $ScriptDir 'questions-index-schema.json'
        if (Test-Path $schemaPath) {
            $schemaBody = Get-Content -Raw $schemaPath
            $code = 0
            $body = ''
            try {
                $resp = Invoke-WebRequest -Uri "$SearchHost/indexes/questions`?api-version=2024-07-01" `
                    -Method PUT -Headers $authHeaders -Body $schemaBody -UseBasicParsing -ErrorAction Stop
                $code = [int]$resp.StatusCode
            } catch {
                if ($_.Exception.Response) {
                    $code = [int]$_.Exception.Response.StatusCode
                    try { $body = $_.ErrorDetails.Message } catch {}
                }
            }
            if ($code -in 200,201,204) {
                Write-Ok "PUT /indexes/questions → $code (upserted)"
            } else {
                $snippet = if ($body) { $body.Substring(0, [Math]::Min(300, $body.Length)) } else { '' }
                Write-Bad "PUT /indexes/questions → $code; body=$snippet"
            }
        } else {
            Write-Bad "questions-index-schema.json not found at $schemaPath"
        }
    }
}

# ---------------------------------------------------------------------------
Write-Section "Summary: $($script:Pass) OK · $($script:Fail) FAIL"
# ---------------------------------------------------------------------------
if ($script:Fail -gt 0) {
    Write-Host 'post-provision hook FAILED' -ForegroundColor Red -NoNewline
    Write-Host ' — see entries above'
    exit 1
}
Write-Host 'post-provision hook PASSED' -ForegroundColor Green
exit 0
