# Install / Uninstall / Run the v3 sleep-time consolidation Task Scheduler job.
#
# Runs `scripts/memory_consolidation_runner.py` every 6 hours
# (03:00 / 09:00 / 15:00 / 21:00 local) so the workspace insight
# review queue picks up recurring themes from the last 24h of
# episodes. Catches up on resume from sleep via -StartWhenAvailable.
#
# The scheduled task spawns ``pythonw.exe`` directly (no PowerShell
# wrapper). ``pythonw.exe`` is the windowless variant of python.exe —
# it never allocates a console, so the operator does not see a
# console window flash on each trigger. Earlier revisions used
# ``powershell.exe -File <wrapper.ps1>`` which always flashed a
# console window during process creation, even with
# ``-WindowStyle Hidden``.
#
# Examples:
#
#   .\memory_consolidation_task.ps1 -Action Install
#   .\memory_consolidation_task.ps1 -Action Run        # one-shot, no task
#   .\memory_consolidation_task.ps1 -Action Uninstall
#
# The job runs as the interactive user (no Service account) so it can
# read the per-user workspaces registry under ~/.agent_memory/.
param(
    [ValidateSet("Install", "Uninstall", "Status", "Run")]
    [string]$Action = "Status",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "",
    [string]$TaskName = "agent-memory-lite-consolidation"
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

$RunnerScript = Join-Path $RepoRoot "scripts\memory_consolidation_runner.py"
$LogDir = Join-Path $env:USERPROFILE ".agent_memory\logs"
$LogPath = Join-Path $LogDir "consolidation.log"
# Legacy wrapper from earlier revisions — removed on Install/Uninstall.
$LegacyWrapperPath = Join-Path $env:USERPROFILE ".agent_memory\run_consolidation.ps1"

if ($Action -eq "Install") {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    # Clean up the old PowerShell wrapper if present — it caused the flash.
    if (Test-Path -LiteralPath $LegacyWrapperPath) {
        Remove-Item -LiteralPath $LegacyWrapperPath -Force
    }

    # Argument list passed directly to pythonw.exe.
    $ActionArg = "`"$RunnerScript`" --json --log-file `"$LogPath`""
    $ScheduledAction = New-ScheduledTaskAction `
        -Execute $PythonW `
        -Argument $ActionArg `
        -WorkingDirectory $RepoRoot

    # Four daily triggers at 03 / 09 / 15 / 21 local time.
    $Triggers = @(
        (New-ScheduledTaskTrigger -Daily -At 3:00am),
        (New-ScheduledTaskTrigger -Daily -At 9:00am),
        (New-ScheduledTaskTrigger -Daily -At 3:00pm),
        (New-ScheduledTaskTrigger -Daily -At 9:00pm)
    )

    $Settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries `
        -Hidden `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $ScheduledAction `
        -Trigger $Triggers `
        -Settings $Settings `
        -RunLevel Limited | Out-Null
    Write-Output ("Installed scheduled task `"$TaskName`" (pythonw.exe, 4 daily triggers, no console flash).")
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
    task_name      = $TaskName
    state          = $Task.State.ToString()
    last_run_time  = $Info.LastRunTime
    next_run_time  = $Info.NextRunTime
    last_task_result = $Info.LastTaskResult
    log_path       = $LogPath
    pythonw        = $PythonW
} | ConvertTo-Json -Depth 6
