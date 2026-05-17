# Install / Uninstall / Run the v3 digest worker Task Scheduler job.
#
# Drains the digest queue (~/.agent_memory/digest_queue.jsonl) every 5
# minutes via `memory_digest_worker_runner.py drain_queue` so the
# PostToolUse hook never accumulates more than ~5 min of pending work.
# Catches up on resume from sleep via -StartWhenAvailable.
#
# Examples:
#
#   .\memory_digest_worker_task.ps1 -Action Install
#   .\memory_digest_worker_task.ps1 -Action Run        # one-shot, no task
#   .\memory_digest_worker_task.ps1 -Action Uninstall
#
# Runs as the interactive user so it can read the per-user
# ~/.agent_memory/digest_queue.jsonl.
param(
    [ValidateSet("Install", "Uninstall", "Status", "Run")]
    [string]$Action = "Status",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "",
    [string]$TaskName = "agent-memory-lite-digest-worker",
    [int]$IntervalMinutes = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$PathValue) {
    if (Test-Path -LiteralPath $PathValue) {
        return (Resolve-Path -LiteralPath $PathValue).Path
    }
    return [System.IO.Path]::GetFullPath($PathValue)
}

$RepoRoot = Resolve-FullPath $RepoRoot
if ([string]::IsNullOrWhiteSpace($Python)) {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
}

$RunnerScript = Join-Path $RepoRoot "scripts\memory_digest_worker_runner.py"
$LogDir = Join-Path $env:USERPROFILE ".agent_memory\logs"
$LogPath = Join-Path $LogDir "digest_worker.log"
$WrapperPath = Join-Path $env:USERPROFILE ".agent_memory\run_digest_worker.ps1"

if ($Action -eq "Install") {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $Wrapper = @"
Set-Location -LiteralPath "$RepoRoot"
& "$Python" "$RunnerScript" --json *>> "$LogPath" 2>&1
"@
    Set-Content -LiteralPath $WrapperPath -Value $Wrapper -Encoding UTF8

    $ActionArg = "-NoProfile -ExecutionPolicy Bypass -File `"$WrapperPath`""
    $ScheduledAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ActionArg

    # Every N minutes for the next ~10 years (Windows Task Scheduler
    # rejects [TimeSpan]::MaxValue with HRESULT 0x80041318).
    $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $Settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $ScheduledAction `
        -Trigger $Trigger `
        -Settings $Settings `
        -RunLevel Limited | Out-Null
    Write-Output ("Installed scheduled task `"$TaskName`" (every $IntervalMinutes min).")
    return
}

if ($Action -eq "Uninstall") {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $WrapperPath) {
        Remove-Item -LiteralPath $WrapperPath -Force
    }
    Write-Output ("Uninstalled scheduled task `"$TaskName`".")
    return
}

if ($Action -eq "Run") {
    Set-Location -LiteralPath $RepoRoot
    & $Python $RunnerScript --json
    return
}

# Default: Status
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $Task) {
    Write-Output ("Scheduled task `"$TaskName`" is NOT installed. Run with -Action Install.")
    return
}
$Info = Get-ScheduledTaskInfo -TaskName $TaskName
@{
    task_name        = $TaskName
    state            = $Task.State.ToString()
    last_run_time    = $Info.LastRunTime
    next_run_time    = $Info.NextRunTime
    last_task_result = $Info.LastTaskResult
    log_path         = $LogPath
} | ConvertTo-Json -Depth 6
