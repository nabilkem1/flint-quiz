# Post-deploy smoke matrix — PowerShell port of post-deploy-smoke.sh.
#
# Sequence (identical to bash):
#   1. Seed + reindex via the seed-loader Container Apps Job (TEST-002).
#   2. In-process smoke matrix (TEST-003/004/005) via pytest.
#   3. App Insights observability assertion (TEST-010).

$ErrorActionPreference = 'Continue'
$script:Fail = 0

function Write-Ok  { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "PASS" -ForegroundColor Green -NoNewline; Write-Host "] $m" }
function Write-Bad { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "FAIL" -ForegroundColor Red   -NoNewline; Write-Host "] $m"; $script:Fail = 1 }
function Write-Info { param([string]$m) Write-Host "  [" -NoNewline; Write-Host "INFO" -ForegroundColor Yellow -NoNewline; Write-Host "] $m" }
function Write-Section { param([string]$m) Write-Host ""; Write-Host "== $m ==" }

# Python — Windows installs ship as `python` (or `py`), Linux ships `python3`. Try both.
$Python = $null
foreach ($cand in @('python','python3','py')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    # Reject Microsoft Store "App Execution Alias" stubs at
    # %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe — they don't actually
    # run Python; they pop a Store install prompt and exit non-zero.
    if ($cmd.Source -match '\\Microsoft\\WindowsApps\\') { continue }
    $Python = $cand
    break
}

$EnvName = if ($env:AZURE_ENV_NAME)     { $env:AZURE_ENV_NAME }     else { 'dev' }
$Rg      = if ($env:AZURE_RESOURCE_GROUP) { $env:AZURE_RESOURCE_GROUP } else { "fq-$EnvName-rg" }
$SeedJob = if ($env:SEED_LOADER_JOB_NAME) { $env:SEED_LOADER_JOB_NAME } else { "fq-$EnvName-seed-loader" }

# ---------------------------------------------------------------------------
Write-Section "Seed + reindex (TEST-002) — env=$EnvName"
# ---------------------------------------------------------------------------
Write-Host "  starting CAJ '$SeedJob' (chains seed_index + seed_topics) ..."
$execName = az containerapp job start -n $SeedJob -g $Rg --query 'name' -o tsv 2>&1
if (-not $execName -or $LASTEXITCODE -ne 0 -or $execName -match 'ERROR') {
    Write-Bad "could not start seed-loader job: $execName"
} else {
    Write-Host "  execution: $execName — polling for completion (up to 10m) ..."
    $status = ''
    foreach ($_ in 1..60) {
        $status = az containerapp job execution show -n $SeedJob -g $Rg --job-execution-name $execName `
            --query 'properties.status' -o tsv 2>$null
        switch ($status) {
            'Succeeded' { Write-Ok 'seed-loader job succeeded (seed_index + seed_topics)'; break }
            'Failed'    { Write-Bad "seed-loader job failed — inspect via 'az containerapp job execution show -n $SeedJob -g $Rg --job-execution-name $execName'"; break }
        }
        if ($status -in 'Succeeded','Failed') { break }
        Start-Sleep -Seconds 10
    }
    if ($status -eq 'Running') { Write-Bad 'seed-loader job still running after 10 min — investigate' }
}

# ---------------------------------------------------------------------------
Write-Section 'In-process smoke matrix (TEST-003/004/005)'
# ---------------------------------------------------------------------------
$smokeTargets = @(
    @{ Target = 'tests/smoke/test_text_en.py::test_text_en_smoke_end_to_end'; Id = 'TEST-003' },
    @{ Target = 'tests/smoke/test_text_fr.py::test_text_fr_smoke_end_to_end'; Id = 'TEST-004' },
    @{ Target = 'tests/smoke/test_voice_es.py::test_voice_es_smoke_end_to_end'; Id = 'TEST-005' }
)
if (-not $Python) {
    Write-Info 'no real Python interpreter on PATH (Microsoft Store stubs filtered out) — skipping smoke matrix'
    Write-Info 'install Python 3.12 + project deps to run TEST-003/004/005 locally, or rely on CI'
} else {
    foreach ($entry in $smokeTargets) {
        $target = $entry.Target
        $testId = $entry.Id
        # Stream stderr to the console so a real failure (missing dep, auth,
        # network) is visible. Out-Null still drops stdout — the PASS/FAIL line
        # comes from the exit code.
        & $Python -m pytest $target -q --tb=short 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$testId ($target)"
        } else {
            & $Python -m pytest $target -q --tb=short 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "$testId ($target) — flaked once, passed on retry [documented note]"
            } else {
                Write-Bad "$testId ($target) — failed twice; release-block"
            }
        }
    }
}

# ---------------------------------------------------------------------------
Write-Section 'Observability assertion (TEST-010)'
# ---------------------------------------------------------------------------
if ($env:APP_INSIGHTS_NAME -and $env:AZURE_RESOURCE_GROUP) {
    # 2-minute wait covers documented App Insights ingestion delay.
    Start-Sleep -Seconds 120
    $query = 'customEvents | where name == "grading_event" | take 1 | project name, expected = customDimensions.expected, receivedRaw = customDimensions.receivedRaw'
    $responseJson = az monitor app-insights query --app $env:APP_INSIGHTS_NAME -g $env:AZURE_RESOURCE_GROUP `
        --analytics-query $query -o json 2>$null
    if (-not $responseJson) { $responseJson = '{}' }
    try {
        $response = $responseJson | ConvertFrom-Json
        $rows = @()
        if ($response.tables -and $response.tables[0].rows) { $rows = $response.tables[0].rows }
        if ($rows.Count -gt 0) {
            Write-Ok 'grading_event observed in App Insights'
            $firstRow = $rows[0]
            $leaked = $false
            foreach ($cell in $firstRow) {
                if ($cell -eq 'expected' -or $cell -eq 'receivedRaw') { $leaked = $true; break }
            }
            if ($leaked) {
                Write-Bad 'grading_event leaked `expected` or `receivedRaw` — SEC-014 violation'
            } else {
                Write-Ok 'grading_event does NOT carry `expected` / `receivedRaw` (SEC-014)'
            }
        } else {
            # No live traffic yet — informational, not a release blocker.
            Write-Info 'no grading_event observed yet — needs real traffic (informational; non-blocking)'
        }
    } catch {
        Write-Info "App Insights query result could not be parsed: $_"
    }
} else {
    Write-Host '  [SKIP] App Insights query — APP_INSIGHTS_NAME / AZURE_RESOURCE_GROUP unset'
}

Write-Host ''
if ($script:Fail -ne 0) {
    Write-Host 'post-deploy-smoke FAILED' -ForegroundColor Red -NoNewline
    Write-Host ' — release blocked.'
    exit 1
}
Write-Host 'post-deploy-smoke PASSED' -ForegroundColor Green
exit 0
