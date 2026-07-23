param(
    [string]$SessionId = "019f7578-4bc8-75c2-9d6d-07ab198399ae",
    [datetime]$RunAt = (Get-Date "2026-07-20 00:26:00"),
    [string]$Workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$taskName = "Codex-MiniShop-RCA-Resume-20260720-0026"
$runner = Join-Path $PSScriptRoot "resume-current-task.ps1"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Resume runner does not exist: $runner"
}
if ($RunAt -le (Get-Date)) {
    throw "RunAt must be in the future: $RunAt"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -SessionId `"$SessionId`" -Workspace `"$Workspace`""
$trigger = New-ScheduledTaskTrigger -Once -At $RunAt
$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Resume the MiniShop RCA project in the current Codex session." `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $taskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
[pscustomobject]@{
    TaskName = $task.TaskName
    State = $task.State
    NextRunTime = $taskInfo.NextRunTime
    SessionId = $SessionId
    Workspace = $Workspace
} | Format-List
