param(
    [switch]$Upgrade
)

$ErrorActionPreference = "Stop"
$RequiredUvVersion = "0.11.31"
$Workspace = Split-Path -Parent $PSScriptRoot
$MiniShop = Get-ChildItem -LiteralPath $Workspace -Directory |
    Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName "app\scenario_manifest.py")
    } |
    Select-Object -First 1
if ($null -eq $MiniShop) {
    throw "Could not locate the MiniShop project"
}

$ActualUvVersion = (& uv --version 2>$null)
if ($LASTEXITCODE -ne 0 -or $ActualUvVersion -notmatch "^uv $([regex]::Escape($RequiredUvVersion))\b") {
    throw "uv $RequiredUvVersion is required. Install it with: python -m pip install uv==$RequiredUvVersion"
}

$LockArguments = @("lock")
if ($Upgrade) {
    $LockArguments += "--upgrade"
}

Push-Location $Workspace
try {
    & uv @LockArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Could not update the platform uv.lock"
    }

    Push-Location $MiniShop.FullName
    try {
        & uv @LockArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Could not update the MiniShop uv.lock"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}

Write-Host "Dependency locks are current."
