param(
    [switch]$KeepStack
)

$ErrorActionPreference = "Stop"
$ComposeFile = Join-Path $PSScriptRoot "docker-compose.yml"
# Docker Desktop can default Compose builds to Bake/BuildKit. The classic build
# path avoids a session-header encoding failure in non-ASCII workspaces.
$env:COMPOSE_BAKE = "false"
$env:DOCKER_BUILDKIT = "0"

docker compose -f $ComposeFile --profile e2e down -v --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "Could not reset the MiniShop E2E stack"
}

docker compose -f $ComposeFile --profile e2e up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "Could not start the MiniShop E2E stack"
}

docker compose -f $ComposeFile wait e2e-runner
$RunnerExitCode = $LASTEXITCODE

if ($RunnerExitCode -ne 0) {
    docker compose -f $ComposeFile logs --no-color --tail 300
}

if (-not $KeepStack) {
    docker compose -f $ComposeFile --profile e2e down -v --remove-orphans
}

if ($RunnerExitCode -ne 0) {
    throw "MiniShop E2E runner failed with exit code $RunnerExitCode"
}

Write-Host "MiniShop E2E passed. Result: $PSScriptRoot\artifacts\results.json"
