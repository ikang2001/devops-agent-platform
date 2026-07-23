param(
    [string]$Python = "python",
    [ValidateSet("auto", "docker", "podman")]
    [string]$ContainerCli = "auto",
    [string]$DataRoot = "",
    [switch]$IncludeOidc,
    [switch]$Down
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$ComposeFile = Join-Path $PSScriptRoot "docker-compose.step4.yml"
$ResolvedDataRoot = $DataRoot
if ([string]::IsNullOrWhiteSpace($ResolvedDataRoot)) {
    $ResolvedDataRoot = Join-Path $Root ".tmp\step4-acceptance-data"
}
New-Item -ItemType Directory -Path $ResolvedDataRoot -Force | Out-Null
$ResolvedDataRoot = (Resolve-Path -LiteralPath $ResolvedDataRoot).Path
foreach ($child in @("redpanda")) {
    New-Item -ItemType Directory -Path (Join-Path $ResolvedDataRoot $child) -Force | Out-Null
}
$env:STEP4_ACCEPTANCE_DATA_ROOT = $ResolvedDataRoot.Replace("\", "/")

function Require-Command {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "$Name is required."
    }
}

function Require-EnvironmentVariable {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Missing required environment variable: $Name"
    }
}

function Resolve-ComposeCommand {
    param([string]$PreferredCli)
    function Get-ExecutablePath {
        param($Command)
        if ($null -eq $Command) {
            return $null
        }
        if ($Command.PSObject.Properties.Name -contains "Source") {
            return $Command.Source
        }
        if ($Command.PSObject.Properties.Name -contains "FullName") {
            return $Command.FullName
        }
        return [string]$Command
    }
    $docker = Get-Command "docker" -ErrorAction SilentlyContinue
    $podman = Get-Command "podman" -ErrorAction SilentlyContinue
    if ($null -eq $podman) {
        $localPodman = Join-Path $env:LOCALAPPDATA "Programs\Podman\podman.exe"
        if (Test-Path -LiteralPath $localPodman) {
            $podman = Get-Item -LiteralPath $localPodman
        }
    }
    if ($PreferredCli -eq "docker") {
        if ($null -eq $docker) {
            throw "Docker CLI is required when -ContainerCli docker is selected."
        }
        return @{
            Command = Get-ExecutablePath $docker
            Prefix = @("compose")
            Name = "docker compose"
        }
    }
    if ($PreferredCli -eq "podman") {
        if ($null -eq $podman) {
            throw "Podman CLI is required when -ContainerCli podman is selected."
        }
        return @{
            Command = Get-ExecutablePath $podman
            Prefix = @("compose")
            Name = "podman compose"
        }
    }
    if ($null -ne $docker) {
        & $docker.Source info *> $null
        if ($LASTEXITCODE -eq 0) {
            return @{
                Command = Get-ExecutablePath $docker
                Prefix = @("compose")
                Name = "docker compose"
            }
        }
    }
    if ($null -ne $podman) {
        return @{
            Command = Get-ExecutablePath $podman
            Prefix = @("compose")
            Name = "podman compose"
        }
    }
    if ($null -ne $docker) {
        return @{
            Command = Get-ExecutablePath $docker
            Prefix = @("compose")
            Name = "docker compose"
        }
    }
    throw "Docker CLI or Podman CLI with Compose support is required. Install Docker Desktop, Podman Desktop, or another compatible container CLI first."
}

$Compose = Resolve-ComposeCommand -PreferredCli $ContainerCli
$ComposeCommand = $Compose["Command"]
$ComposePrefix = $Compose["Prefix"]
$ComposeName = $Compose["Name"]

if ($Down) {
    $composeArgs = @($ComposePrefix) + @("-f", $ComposeFile, "down", "-v")
    & $ComposeCommand @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Could not stop local Step 4 acceptance stack"
    }
    return
}

$composeArgs = @($ComposePrefix) + @("-f", $ComposeFile, "up", "-d")
& $ComposeCommand @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw "Could not start local Step 4 acceptance stack with $ComposeName"
}

$env:DEVOPS_AGENT_TEST_POSTGRES_URL = "postgresql+psycopg://devops_agent:devops_agent@localhost:15432/devops_agent"
$env:DEVOPS_AGENT_TEST_KAFKA_BOOTSTRAP_SERVERS = "localhost:19092"
$env:DEVOPS_AGENT_TEST_TENANT_ID = "step4-local"
$env:DEVOPS_AGENT_TEST_PROMETHEUS_URL = "http://localhost:18080"
$env:DEVOPS_AGENT_TEST_LOKI_URL = "http://localhost:18080"
$env:DEVOPS_AGENT_TEST_TEMPO_URL = "http://localhost:18080"
$env:DEVOPS_AGENT_TEST_LLM_BASE_URL = "http://localhost:18080"
$env:DEVOPS_AGENT_TEST_LLM_API_KEY = "local-acceptance-key"
$env:DEVOPS_AGENT_TEST_LLM_MODEL = "local-rca-model"
$env:DEVOPS_AGENT_TEST_TICKETING_ENDPOINT_URL = "http://localhost:18080/api/tickets"
$env:DEVOPS_AGENT_RUN_LIVE_TESTS = "1"
$env:DEVOPS_AGENT_DATABASE_URL = $env:DEVOPS_AGENT_TEST_POSTGRES_URL

@'
import asyncio
import os
import time
import urllib.request

from aiokafka import AIOKafkaProducer
import psycopg


deadline = time.monotonic() + 120


def remaining() -> float:
    return max(0.1, deadline - time.monotonic())


def wait_postgres() -> None:
    url = os.environ["DEVOPS_AGENT_TEST_POSTGRES_URL"].replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )
    last_error = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(url, connect_timeout=3) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"PostgreSQL did not become ready: {last_error}")


async def wait_kafka() -> None:
    last_error = None
    while time.monotonic() < deadline:
        producer = AIOKafkaProducer(
            bootstrap_servers=os.environ[
                "DEVOPS_AGENT_TEST_KAFKA_BOOTSTRAP_SERVERS"
            ],
            request_timeout_ms=3000,
        )
        try:
            await producer.start()
            await producer.stop()
            return
        except Exception as exc:
            last_error = exc
            try:
                await producer.stop()
            except Exception:
                pass
            await asyncio.sleep(2)
    raise RuntimeError(f"Kafka did not become ready: {last_error}")


def wait_http() -> None:
    url = "http://localhost:18080/healthz"
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"HTTP sandbox did not become ready: {last_error}")


wait_postgres()
asyncio.run(wait_kafka())
wait_http()
print("LOCAL_ACCEPTANCE_STACK_READY=1")
'@ | & $Python -

if ($LASTEXITCODE -ne 0) {
    throw "Local Step 4 acceptance stack did not become ready"
}

if ($IncludeOidc) {
    Require-EnvironmentVariable "DEVOPS_AGENT_TEST_OIDC_ISSUER"
    Require-EnvironmentVariable "DEVOPS_AGENT_TEST_OIDC_AUDIENCE"
    Require-EnvironmentVariable "DEVOPS_AGENT_TEST_OIDC_JWKS_URL"
    Require-EnvironmentVariable "DEVOPS_AGENT_TEST_OIDC_TOKEN"
    & $Python -m pytest tests/live -m live -q
} else {
    Write-Warning "Skipping live OIDC contract. Set OIDC sandbox variables and pass -IncludeOidc for full target acceptance."
    & $Python -m pytest tests/live -m live -q -k "not oidc"
}

if ($LASTEXITCODE -ne 0) {
    throw "Local Step 4 acceptance failed"
}

Write-Host "Local Step 4 acceptance passed."
