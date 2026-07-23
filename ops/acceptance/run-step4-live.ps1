param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

function Require-EnvironmentVariable {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Missing required environment variable: $Name"
    }
}

Require-EnvironmentVariable "DEVOPS_AGENT_TEST_POSTGRES_URL"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_KAFKA_BOOTSTRAP_SERVERS"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_TENANT_ID"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_PROMETHEUS_URL"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_LOKI_URL"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_TEMPO_URL"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_OIDC_ISSUER"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_OIDC_AUDIENCE"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_OIDC_JWKS_URL"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_OIDC_TOKEN"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_LLM_BASE_URL"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_LLM_API_KEY"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_LLM_MODEL"
Require-EnvironmentVariable "DEVOPS_AGENT_TEST_TICKETING_ENDPOINT_URL"

$env:DEVOPS_AGENT_RUN_LIVE_TESTS = "1"
$env:DEVOPS_AGENT_DATABASE_URL = $env:DEVOPS_AGENT_TEST_POSTGRES_URL

& $Python -m pytest tests/live -m live -q
if ($LASTEXITCODE -ne 0) {
    throw "Step 4 live dependency acceptance failed"
}

Write-Host "Step 4 live dependency acceptance passed."
