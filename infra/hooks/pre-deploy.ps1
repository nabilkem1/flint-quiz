# Pre-deploy checklist — PowerShell port of pre-deploy.sh.
#
# Mirrors the bash hook: same six checks, same exit codes, same human
# output. Wired to azure.yaml:hooks.preprovision (windows variant).

$ErrorActionPreference = 'Continue'
$script:Fail = 0

function Write-Ok      { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "OK  " -ForegroundColor Green -NoNewline; Write-Host "] $m" }
function Write-Bad     { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "FAIL" -ForegroundColor Red   -NoNewline; Write-Host "] $m"; $script:Fail = 1 }
function Write-Warn    { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "WARN" -ForegroundColor Yellow -NoNewline; Write-Host "] $m" }
function Write-Section { param([string]$m) Write-Host ""; Write-Host "== $m ==" }

# ---------------------------------------------------------------------------
Write-Section 'Required environment variables (azd populates these)'
# ---------------------------------------------------------------------------
foreach ($v in @('AZURE_ENV_NAME','AZURE_LOCATION')) {
    $val = [Environment]::GetEnvironmentVariable($v)
    if ($val) { Write-Ok "$v = $val" }
    else      { Write-Bad "env $v is not set — run 'azd env new' first" }
}

$EnvName = if ($env:AZURE_ENV_NAME) { $env:AZURE_ENV_NAME } else { 'dev' }

# ---------------------------------------------------------------------------
Write-Section "Parameter file: infra/main.parameters.$EnvName.json"
# ---------------------------------------------------------------------------
$ParamFile = "infra/main.parameters.$EnvName.json"
if (-not (Test-Path $ParamFile)) {
    if (Test-Path 'infra/main.parameters.json') {
        Write-Ok "parameter file $ParamFile not found — using fallback infra/main.parameters.json"
        $ParamFile = 'infra/main.parameters.json'
    } else {
        Write-Bad "neither $ParamFile nor infra/main.parameters.json present"
        exit 1
    }
} else {
    Write-Ok "parameter file $ParamFile present"
}

$RequiredParamKeys = @(
    'environmentName','location','prefix','supportedLanguages',
    'modelDeploymentName','modelName','modelVersion',
    'cosmosSessionsTtlDays','auditTtlDays',
    'voiceMaxSessionMinutes','voiceIdleSeconds',
    'featuresApim','tagOwner','tagCostCenter'
)

try {
    $paramsJson = Get-Content -Raw $ParamFile | ConvertFrom-Json
    $paramsObj  = $paramsJson.parameters
    $paramNames = @($paramsObj.PSObject.Properties.Name)
    foreach ($key in $RequiredParamKeys) {
        if ($paramNames -contains $key) {
            Write-Ok "param $key populated"
        } else {
            Write-Bad "param $key missing from $ParamFile"
        }
    }
} catch {
    Write-Bad "could not parse $ParamFile as JSON: $_"
}

# ---------------------------------------------------------------------------
Write-Section 'Referenced Bicep modules exist on disk'
# ---------------------------------------------------------------------------
if (Test-Path 'infra/main.bicep') {
    $mainBicep = Get-Content 'infra/main.bicep'
    $modules = $mainBicep |
        Select-String -Pattern "^\s*module\s+[A-Za-z0-9_]+\s+'([^']+)'" |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Sort-Object -Unique
    foreach ($module in $modules) {
        $pathRel = "infra/$module"
        if (Test-Path $pathRel) {
            Write-Ok "module $module"
        } else {
            Write-Bad "module $module referenced in main.bicep but not on disk"
        }
    }
} else {
    Write-Bad 'infra/main.bicep missing — cannot resolve module references'
}

# ---------------------------------------------------------------------------
Write-Section 'Deployer can enumerate role assignments'
# ---------------------------------------------------------------------------
$azCmd = Get-Command az -ErrorAction SilentlyContinue
if ($azCmd) {
    $null = az role assignment list --all --query '[0].id' -o tsv 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok 'az role assignment list works (deployer has read access)'
    } else {
        Write-Bad 'az role assignment list failed — pre-provision RBAC check would not run'
    }
} else {
    Write-Warn 'az CLI not installed — skipping role-assignment probe (CI MUST have it)'
}

# ---------------------------------------------------------------------------
Write-Section 'Repeat deploys: existing AppConfig + Key Vault probes (skipped on fresh)'
# ---------------------------------------------------------------------------
$AppConfigName = $env:APP_CONFIG_NAME
$KeyVaultName  = $env:KEY_VAULT_NAME
$Rg            = $env:AZURE_RESOURCE_GROUP

if ($AppConfigName -and $Rg) {
    $null = az appconfig show -n $AppConfigName -g $Rg --query name -o tsv 2>$null
    if ($LASTEXITCODE -eq 0) {
        $null = az appconfig kv show --name $AppConfigName --key 'model:deploymentName' --auth-mode login --query value -o tsv 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "AppConfig key 'model:deploymentName' readable"
        } else {
            Write-Warn "AppConfig key 'model:deploymentName' not present (expected on fresh deploys; seeded post-provision)"
        }
    } else {
        Write-Warn "AppConfig $AppConfigName not yet provisioned (fresh deploy)"
    }
}

if ($KeyVaultName -and $Rg) {
    $null = az keyvault show -n $KeyVaultName -g $Rg --query name -o tsv 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Key Vault $KeyVaultName reachable"
    } else {
        Write-Warn "Key Vault $KeyVaultName not yet provisioned (fresh deploy)"
    }
}

# ---------------------------------------------------------------------------
Write-Section 'Stale SERVICE_*_IMAGE_NAME guard (post-azd-down recovery)'
# ---------------------------------------------------------------------------
function Clear-StaleImageVar {
    param([string]$VarName)
    $val = [Environment]::GetEnvironmentVariable($VarName)
    if (-not $val) { return }

    # `<acr-host>/<repo>:<tag>`
    $slash = $val.IndexOf('/')
    if ($slash -lt 0) { return }
    $acrHost      = $val.Substring(0, $slash)
    $repoAndTag   = $val.Substring($slash + 1)
    $colon        = $repoAndTag.LastIndexOf(':')
    if ($colon -lt 0) { return }
    $repo         = $repoAndTag.Substring(0, $colon)
    $tag          = $repoAndTag.Substring($colon + 1)
    $dot          = $acrHost.IndexOf('.')
    $acrName      = if ($dot -ge 0) { $acrHost.Substring(0, $dot) } else { $acrHost }

    $tags = az acr manifest show-tags -n $acrName --repository $repo --query "[?name=='$tag']" -o tsv 2>$null
    if ($LASTEXITCODE -eq 0 -and $tags -and ($tags -match [regex]::Escape($tag))) {
        Write-Ok "$VarName → $tag (manifest present in $acrName)"
    } else {
        Write-Warn "$VarName → $tag not in ACR $acrName — clearing so bootstrap fallback applies"
        try { azd env set $VarName '' 2>$null | Out-Null } catch {}
        [Environment]::SetEnvironmentVariable($VarName, $null)
    }
}

foreach ($var in @('SERVICE_QUIZ_AGENT_IMAGE_NAME','SERVICE_SEED_LOADER_IMAGE_NAME','SERVICE_SWEEPER_IMAGE_NAME')) {
    Clear-StaleImageVar -VarName $var
}

# ---------------------------------------------------------------------------
Write-Section 'Environment guard: prevent prod-seed-in-dev mistakes'
# ---------------------------------------------------------------------------
$ExpectedRgPattern = "^fq-$EnvName-rg$"
if ($Rg) {
    if ($Rg -match $ExpectedRgPattern) {
        Write-Ok "AZURE_RESOURCE_GROUP=$Rg matches expected pattern for env=$EnvName"
    } else {
        Write-Bad "AZURE_RESOURCE_GROUP=$Rg does NOT match $ExpectedRgPattern — wrong env?"
    }
}

Write-Host ''
if ($script:Fail -ne 0) {
    Write-Host 'pre-deploy hook FAILED' -ForegroundColor Red -NoNewline
    Write-Host ' — fix the failures above before `azd up`.'
    exit 1
}
Write-Host 'pre-deploy hook PASSED' -ForegroundColor Green
exit 0
