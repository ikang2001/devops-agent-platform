[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$TenantId = "reference-tenant",
    [string]$Duration = "5m",
    [string]$TargetLabel = "reference-staging"
)

$ErrorActionPreference = "Stop"
if (-not $env:DEVOPS_AGENT_LOAD_WEBHOOK_SECRET) {
    throw "DEVOPS_AGENT_LOAD_WEBHOOK_SECRET must be set in the process environment"
}
if (Test-Path -LiteralPath $OutputDirectory) {
    throw "OutputDirectory already exists; use a new immutable evidence directory"
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null

$durationSeconds = if ($Duration -match '^([0-9]+(?:\.[0-9]+)?)s$') {
    [double]$Matches[1]
} elseif ($Duration -match '^([0-9]+(?:\.[0-9]+)?)m$') {
    [double]$Matches[1] * 60
} elseif ($Duration -match '^([0-9]+(?:\.[0-9]+)?)h$') {
    [double]$Matches[1] * 3600
} else {
    throw "Duration must use k6 units such as 300s, 5m, or 1h"
}

foreach ($rate in @(100, 500, 1000)) {
    $env:BASE_URL = $BaseUrl.TrimEnd("/")
    $env:ALERT_WEBHOOK_SECRET = $env:DEVOPS_AGENT_LOAD_WEBHOOK_SECRET
    $env:TENANT_ID = $TenantId
    $env:DURATION = $Duration
    $env:ALERT_RATE = "$rate"
    $env:ALERT_TIME_UNIT = "1m"
    (Invoke-WebRequest -UseBasicParsing -Uri "$($BaseUrl.TrimEnd('/'))/metrics" -TimeoutSec 10).Content |
        Set-Content -LiteralPath "$OutputDirectory/metrics-$rate-before.prom" -Encoding UTF8
    k6 run --summary-export "$OutputDirectory/load-$rate.json" `
        "$PSScriptRoot/step4-acceptance.js"
    if ($LASTEXITCODE -ne 0) {
        throw "k6 failed at $rate alerts/min"
    }
    Start-Sleep -Seconds 10
    (Invoke-WebRequest -UseBasicParsing -Uri "$($BaseUrl.TrimEnd('/'))/metrics" -TimeoutSec 10).Content |
        Set-Content -LiteralPath "$OutputDirectory/metrics-$rate-after.prom" -Encoding UTF8
}

uv run python -m ops.load.run_load --mode live `
    --input-directory $OutputDirectory `
    --target-label $TargetLabel `
    --duration-seconds $durationSeconds `
    --output "$OutputDirectory/load-report.json"
if ($LASTEXITCODE -ne 0) {
    throw "load report aggregation failed"
}
