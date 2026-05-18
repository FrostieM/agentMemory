# Install / Uninstall / Run the v3 digest worker Task Scheduler job.
#
# Drains the digest queue (~/.agent_memory/digest_queue.jsonl) every 5
# minutes via `memory_digest_worker_runner.py drain_queue` so the
# PostToolUse hook never accumulates more than ~5 min of pending work.
# Catches up on resume from sleep via -StartWhenAvailable.
#
# The scheduled task spawns ``pythonw.exe`` directly (no PowerShell
# wrapper). ``pythonw.exe`` is the windowless variant of python.exe —
# it never allocates a console, so the operator does not see a
# console window flash every 5 min. Earlier revisions used
# ``powershell.exe -File <wrapper.ps1>`` which always flashed a
# console window during process creation, even with
# ``-WindowStyle Hidden``.
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

function Resolve-PythonW([string]$PythonExe) {
    # Derive pythonw.exe from python.exe. Falls back to "pythonw.exe"
    # on PATH so the script still works when $Python is the bare name.
    if ($PythonExe -match '(?i)python\.exe$') {
        $Candidate = $PythonExe -replace '(?i)python\.exe$', 'pythonw.exe'
        if (Test-Path -LiteralPath $Candidate) { return $Candidate }
    }
    return "pythonw.exe"
}

$RepoRoot = Resolve-FullPath $RepoRoot
if ([string]::IsNullOrWhiteSpace($Python)) {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
}
$PythonW = Resolve-PythonW $Python

$RunnerScript = Join-Path $RepoRoot "scripts\memory_digest_worker_runner.py"
$LogDir = Join-Path $env:USERPROFILE ".agent_memory\logs"
$LogPath = Join-Path $LogDir "digest_worker.log"
# Legacy wrapper from earlier revisions — removed on Install/Uninstall.
$LegacyWrapperPath = Join-Path $env:USERPROFILE ".agent_memory\run_digest_worker.ps1"

if ($Action -eq "Install") {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    # Clean up the old PowerShell wrapper if present — it caused the flash.
    if (Test-Path -LiteralPath $LegacyWrapperPath) {
        Remove-Item -LiteralPath $LegacyWrapperPath -Force
    }

    # Argument list passed directly to pythonw.exe. No shell quoting
    # complications because the task scheduler hands the string to
    # CreateProcess as-is.
    $ActionArg = "`"$RunnerScript`" --json --log-file `"$LogPath`""
    $ScheduledAction = New-ScheduledTaskAction `
        -Execute $PythonW `
        -Argument $ActionArg `
        -WorkingDirectory $RepoRoot

    # Every N minutes for the next ~10 years (Windows Task Scheduler
    # rejects [TimeSpan]::MaxValue with HRESULT 0x80041318).
    $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $Settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries `
        -Hidden `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $ScheduledAction `
        -Trigger $Trigger `
        -Settings $Settings `
        -RunLevel Limited | Out-Null
    Write-Output ("Installed scheduled task `"$TaskName`" (pythonw.exe, every $IntervalMinutes min, no console flash).")
    return
}

if ($Action -eq "Uninstall") {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $LegacyWrapperPath) {
        Remove-Item -LiteralPath $LegacyWrapperPath -Force
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
    pythonw          = $PythonW
} | ConvertTo-Json -Depth 6
