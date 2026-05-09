param(
    [string]$WorkspaceId = $env:MEMORY_WORKSPACE_ID,
    [string]$DbPath = "",
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [int]$ThresholdImportance = 9  # tenths; 9 = importance >= 0.9 triggers warning
)

# 1.3.0: pre-commit hook that calls /memory/explain_diff and prints a
# short report listing active decisions whose territory the staged diff
# touches. Non-blocking by default — prints a warning the operator can
# choose to act on. Set $env:MEMORY_PRECOMMIT_BLOCK=1 to abort the
# commit when high-importance decisions match.
#
# Install as a git hook:
#   1. Place this script anywhere accessible.
#   2. In your repo: .git\hooks\pre-commit (no extension) with one line:
#        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<path>\memory_pre_commit.ps1"

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($WorkspaceId)) {
    # Silent no-op when not configured for this repo. Don't block commits
    # in repos that aren't memory-managed.
    exit 0
}

# Get staged diff (cached). git outputs the unified diff to stdout.
$diffOutput = & git diff --cached --no-color 2>$null
if (-not $diffOutput) {
    exit 0
}
$diffText = $diffOutput -join "`n"

# Build request body. JSON-escape the diff via PowerShell's ConvertTo-Json.
$body = @{
    workspace_id = $WorkspaceId
    diff_text = $diffText
    limit_per_section = 10
} | ConvertTo-Json -Depth 4

$headers = @{ "Content-Type" = "application/json" }
if (-not [string]::IsNullOrWhiteSpace($DbPath)) {
    $headers["X-Memory-DB-Path"] = $DbPath
}

try {
    $resp = Invoke-RestMethod -Method Post -Uri "$BaseUrl/memory/explain_diff" `
        -Headers $headers -Body $body -TimeoutSec 5
} catch {
    # Service unreachable — silent skip so the hook never blocks a commit
    # just because the memory service is down.
    Write-Host "[memory-pre-commit] service at $BaseUrl unreachable; skipping" -ForegroundColor DarkYellow
    exit 0
}

if (-not $resp.decisions_matched -or $resp.decisions_matched.Count -eq 0) {
    # Nothing to flag.
    exit 0
}

Write-Host ""
Write-Host "=== memory-pre-commit: $($resp.decisions_matched.Count) decisions match this diff ===" -ForegroundColor Cyan
Write-Host "  files: $($resp.files -join ', ')"
Write-Host ""
$blockingHits = 0
foreach ($d in $resp.decisions_matched) {
    $marker = if ($d.match -eq "declarative") { "[*]" } else { "[~]" }
    $prefix = if ($d.importance -ge ($ThresholdImportance / 10.0)) { "[HIGH]" } else { "      " }
    Write-Host "  $prefix $marker imp=$($d.importance)  $($d.title)"
    Write-Host "         id=$($d.decision_id)  via=$($d.matched_path)"
    if ($d.importance -ge ($ThresholdImportance / 10.0)) {
        $blockingHits += 1
    }
}
Write-Host ""
Write-Host "  legend: [*] declarative match (decision.references)  [~] substring fallback"
Write-Host "  $($resp.summary)" -ForegroundColor DarkGray

if ($env:MEMORY_PRECOMMIT_BLOCK -eq "1" -and $blockingHits -gt 0) {
    Write-Host ""
    Write-Host "[memory-pre-commit] $blockingHits high-importance decisions match." -ForegroundColor Red
    Write-Host "  Set MEMORY_PRECOMMIT_BLOCK=0 to bypass, or use 'git commit --no-verify'."
    exit 1
}
exit 0
