param(
    [ValidateSet("Install", "Uninstall", "Status", "RunNow")]
    [string]$Action = "Status",
    [string]$WorkspaceId = $env:MEMORY_WORKSPACE_ID,
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "",
    [string]$TaskName = "",
    [string]$Time = "03:30",
    [switch]$Apply
)

# 1.2.4 -- wrapper that registers a Windows scheduled task running
# scripts\memory_auto_triage.py against the chosen workspace nightly.
# Without this, capability-link / hygiene debt accumulates between
# manual runs. The task is opt-in (Install action), idempotent on
# re-install, and uses --backup-first so accidental damage is
# recoverable.
#
# 1.2.5: ASCII-only literals so Windows PowerShell 5.1 can parse the
# script regardless of console code page (UTF-8 BOM-less files were
# being read as cp1252, which mangled em-dash characters and threw
# parser errors).

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$PathValue) {
    if (Test-Path -LiteralPath $PathValue) {
        return (Resolve-Path -LiteralPath $PathValue).Path
    }
    return [System.IO.Path]::GetFullPath($PathValue)
}

if ([string]::IsNullOrWhiteSpace($WorkspaceId)) {
    throw "WorkspaceId is required. Pass -WorkspaceId or set MEMORY_WORKSPACE_ID."
}

$RepoRoot = Resolve-FullPath $RepoRoot
$ProjectRoot = Resolve-FullPath $ProjectRoot
if ([string]::IsNullOrWhiteSpace($Python)) {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
}
if ([string]::IsNullOrWhiteSpace($TaskName)) {
    $TaskName = "agent-memory-auto-triage-$WorkspaceId"
}

$MemoryDir = Join-Path $ProjectRoot ".agent_memory"
$LogDir = Join-Path $MemoryDir "logs"
$DbPath = Join-Path $MemoryDir "memory.db"
$LogPath = Join-Path $LogDir "auto_triage.log"
$ScriptPath = Join-Path $RepoRoot "scripts\memory_auto_triage.py"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Auto-triage script not found at $ScriptPath"
}

function Build-Args([bool]$ApplyMutations) {
    $argList = @(
        "`"$ScriptPath`"",
        "--workspace", $WorkspaceId,
        "--db-path", "`"$DbPath`"",
        "--json"
    )
    if ($ApplyMutations) {
        $argList += @("--apply", "--backup-first")
    }
    return $argList -join " "
}

if ($Action -eq "Install") {
    if (-not (Test-Path -LiteralPath $DbPath)) {
        throw "Memory DB not found at $DbPath. Run setup_agent.py --project first."
    }
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $applyMode = if ($Apply) { $true } else { $false }
    $modeLabel = if ($applyMode) { "apply" } else { "dry-run" }
    $argString = Build-Args -ApplyMutations $applyMode
    # Wrap the call so stderr/stdout land in the log file. Every run
    # appends; rotate manually if the log grows. The "&" is wrapped
    # in a string literal so the parser does not treat it as the
    # call-operator outside an expression.
    $cmdLine = "& '$Python' $argString *>> '$LogPath'"
    # -WindowStyle Hidden + -Hidden on settings together suppress the
    # visible PowerShell console flash on the daily 03:30 trigger.
    $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$cmdLine`""
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger `
        -Settings $settings -Force -RunLevel Limited | Out-Null
    Write-Host "[ok] Installed scheduled task '$TaskName' ($modeLabel mode, daily at $Time)"
    Write-Host "      log:    $LogPath"
    Write-Host "      script: $ScriptPath"
    if (-not $applyMode) {
        Write-Host "[!]   dry-run mode by default. Re-install with -Apply to actually write capability_links."
    }
    return
}

if ($Action -eq "Uninstall") {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[ok] Uninstalled scheduled task '$TaskName'"
    } else {
        Write-Host "[skip] No task '$TaskName' to uninstall"
    }
    return
}

if ($Action -eq "RunNow") {
    if (-not (Test-Path -LiteralPath $DbPath)) {
        throw "Memory DB not found at $DbPath"
    }
    $applyMode = if ($Apply) { $true } else { $false }
    $argString = Build-Args -ApplyMutations $applyMode
    Write-Host "[run] $Python $argString"
    Invoke-Expression "& '$Python' $argString"
    return
}

if ($Action -eq "Status") {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "[ok] task '$TaskName' is registered"
        Write-Host "      state:        $($existing.State)"
        Write-Host "      lastRunTime:  $($info.LastRunTime)"
        Write-Host "      lastResult:   $($info.LastTaskResult)"
        Write-Host "      nextRunTime:  $($info.NextRunTime)"
    } else {
        Write-Host "[skip] task '$TaskName' not registered. Install with:"
        Write-Host "      .\scripts\memory_auto_triage_task.ps1 -Action Install -WorkspaceId $WorkspaceId -ProjectRoot $ProjectRoot -Apply"
    }
    return
}
