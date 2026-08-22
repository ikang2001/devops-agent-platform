param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [int]$RunsPerScenario = 5,
    [int]$AgentPort = 18000,
    [int]$MinishopPort = 18080,
    [int]$PrometheusPort = 19090,
    [int]$AlertmanagerPort = 19093,
    [int]$LokiPort = 13100,
    [int]$TempoPort = 13200,
    [ValidateSet("Known", "Holdout", "HoldoutV2")]
    [string]$ScenarioSet = "Known",
    [string]$ScenarioId = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EnvFile = Join-Path $ProjectRoot ".env"
$RootTemp = "D:\DevOpsAgentTemp\v07"
$ScenarioRoot = Get-ChildItem -LiteralPath $ProjectRoot -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "scenarios\public") } |
    Select-Object -First 1
if (-not $ScenarioRoot) {
    throw "MiniShop scenario root was not found"
}
$ScenarioDirectory = switch ($ScenarioSet) {
    "Holdout" { Join-Path $ScenarioRoot.FullName "scenarios\holdout" }
    "HoldoutV2" { Join-Path $ScenarioRoot.FullName "scenarios\holdout-v2" }
    default { Join-Path $ScenarioRoot.FullName "scenarios" }
}
$PublicDirectory = Join-Path $ScenarioDirectory "public"
$PrivateDirectory = Join-Path $ScenarioDirectory "private"

New-Item -ItemType Directory -Path $RootTemp -Force | Out-Null
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$keyLine = Get-Content -LiteralPath $EnvFile -Encoding UTF8 |
    Where-Object { $_ -match '^DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY=' } |
    Select-Object -First 1
if (-not $keyLine) {
    throw "DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY is required in the project .env"
}
$env:DEVOPS_AGENT_BENCHMARK_API_KEY = ($keyLine -split '=', 2)[1].Trim()
if ([string]::IsNullOrWhiteSpace($env:DEVOPS_AGENT_BENCHMARK_API_KEY)) {
    throw "DEVOPS_AGENT_LLM_DASHSCOPE_API_KEY is empty"
}

$env:E2E_AGENT_URL = "http://127.0.0.1:$AgentPort"
$env:E2E_MINISHOP_URL = "http://127.0.0.1:$MinishopPort"
$env:E2E_PROMETHEUS_URL = "http://127.0.0.1:$PrometheusPort"
$env:E2E_ALERTMANAGER_URL = "http://127.0.0.1:$AlertmanagerPort"
$env:E2E_LOKI_URL = "http://127.0.0.1:$LokiPort"
$env:E2E_TEMPO_URL = "http://127.0.0.1:$TempoPort"
$env:E2E_ADMIN_TOKEN = "minishop-e2e-admin-token-change-me-123456"
$env:E2E_WEBHOOK_SECRET = "minishop-e2e-webhook-secret-change-me-123456"

$commit = (git -C $ProjectRoot rev-parse --short HEAD).Trim()
$runnerArgs = @(
    "run", "python", (Join-Path $ProjectRoot "ops\minishop-e2e\run_blackbox.py"),
    "--public", $PublicDirectory,
    "--private", $PrivateDirectory,
    "--output", $OutputRoot,
    "--git-commit", $commit,
    "--provider", "dashscope",
    "--model", "qwen3.7-plus",
    "--base-url", "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "--api-key-env", "DEVOPS_AGENT_BENCHMARK_API_KEY",
    "--runs-per-scenario", $RunsPerScenario,
    "--temporary-root", $RootTemp
)
if (-not [string]::IsNullOrWhiteSpace($ScenarioId)) {
    $runnerArgs += @("--scenario-id", $ScenarioId)
}
& uv @runnerArgs
if ($LASTEXITCODE -ne 0) {
    throw "MiniShop v0.7 host black-box runner failed"
}
