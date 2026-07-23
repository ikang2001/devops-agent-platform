param(
    [Parameter(Mandatory = $true)]
    [uri]$ReadinessUrl,

    [Parameter(Mandatory = $true)]
    [scriptblock]$Inject,

    [Parameter(Mandatory = $true)]
    [scriptblock]$Recover,

    [int]$FailureTimeoutSeconds = 30,
    [int]$RecoveryTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

function Get-ReadinessStatus {
    try {
        $response = Invoke-WebRequest -Uri $ReadinessUrl -TimeoutSec 5
        return [int]$response.StatusCode
    }
    catch {
        if ($null -ne $_.Exception.Response) {
            return [int]$_.Exception.Response.StatusCode
        }
        return 0
    }
}

function Wait-ForReadinessState {
    param(
        [bool]$Ready,
        [int]$TimeoutSeconds
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $status = Get-ReadinessStatus
        $isReady = $status -eq 200
        if ($isReady -eq $Ready) {
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "Readiness did not reach expected state Ready=$Ready"
}

& $Inject
try {
    Wait-ForReadinessState -Ready $false -TimeoutSeconds $FailureTimeoutSeconds
}
finally {
    & $Recover
}
Wait-ForReadinessState -Ready $true -TimeoutSeconds $RecoveryTimeoutSeconds
Write-Host "Fault degradation and recovery acceptance passed."
