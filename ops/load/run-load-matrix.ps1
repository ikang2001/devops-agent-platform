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

foreach ($rate in @(100, 500, 1000)) {
    $env:BASE_URL = $BaseUrl.TrimEnd("/")
    $env:ALERT_WEBHOOK_SECRET = $env:DEVOPS_AGENT_LOAD_WEBHOOK_SECRET
    $env:TENANT_ID = $TenantId
    $env:DURATION = $Duration
    $env:ALERT_RATE = "$rate"
    $env:ALERT_TIME_UNIT = "1m"
    k6 run --summary-export "$OutputDirectory/load-$rate.json" `
        "$PSScriptRoot/step4-acceptance.js"
    if ($LASTEXITCODE -ne 0) {
        throw "k6 failed at $rate alerts/min"
    }
}

uv run python -m ops.load.run_load --mode live `
    --input-directory $OutputDirectory `
    --target-label $TargetLabel `
    --output "$OutputDirectory/load-report.json"
if ($LASTEXITCODE -ne 0) {
    throw "load report aggregation failed"
}
