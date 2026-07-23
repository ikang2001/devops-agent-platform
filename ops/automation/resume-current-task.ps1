param(
    [Parameter(Mandatory = $true)]
    [string]$SessionId,
    [string]$Workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$logDirectory = Join-Path $Workspace ".tmp\scheduled-resume"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$startedAt = Get-Date -Format "yyyyMMdd-HHmmss"
$outputPath = Join-Path $logDirectory "codex-$startedAt-last-message.md"
$logPath = Join-Path $logDirectory "codex-$startedAt.log"

$prompt = @"
Continue completing the current MiniShop DevOps RCA project in this workspace.

Resume from the existing conversation and worktree. Implement and verify, do not only report status:
1. MiniShop to DevOps Agent end-to-end RCA (demo auth, Compose, E2E runner, and Ground Truth evaluation).
2. The module-by-module code tour.
3. Complete validation of all three fault manifests.

Preserve user changes. Make minimal project-consistent edits. Run relevant tests, Ruff, Compose checks, and feasible real E2E. Never claim an unrun check passed. Update docs/backend-engineering-retrospective.md with real evidence and report commands, results, and residual risks.
"@

$codex = Get-Command codex -ErrorAction Stop
& $codex.Source -C $Workspace exec resume $SessionId $prompt `
    --dangerously-bypass-approvals-and-sandbox `
    --skip-git-repo-check `
    --output-last-message $outputPath `
    *> $logPath
$exitCode = $LASTEXITCODE

Add-Content -LiteralPath $logPath -Value "`nCODEX_RESUME_EXIT=$exitCode"
exit $exitCode
