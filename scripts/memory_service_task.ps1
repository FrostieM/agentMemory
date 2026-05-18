param(
    [ValidateSet("Install", "Uninstall", "Start", "Stop", "Status")]
    [string]$Action = "Status",
    [string]$WorkspaceId = $env:MEMORY_WORKSPACE_ID,
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "",
    [string]$TaskName = "",
    [int]$Port = 8765,
    [switch]$HubMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$PathValue) {
    if (Test-Path -LiteralPath $PathValue) {
        return (Resolve-Path -LiteralPath $PathValue).Path
    }
    return [System.IO.Path]::GetFullPath($PathValue)
}

function Write-JsonStatus([hashtable]$Payload) {
    $Payload | ConvertTo-Json -Depth 6
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
    $TaskName = "agent-memory-lite-$WorkspaceId"
}

$MemoryDir = Join-Path $ProjectRoot ".agent_memory"
$LogDir = Join-Path $MemoryDir "logs"
$RunnerPath = Join-Path $MemoryDir "run_memory_service.ps1"
$DbPath = Join-Path $MemoryDir "memory.db"
$VectorPath = Join-Path $MemoryDir "vectors.lance"
$LogPath = Join-Path $LogDir "memory_service.log"

if ($Action -eq "Install") {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $StrictValue = if ($HubMode) { "0" } else { "1" }
    $HubValue = if ($HubMode) { "1" } else { "0" }
    # NOTE: do NOT set `$ErrorActionPreference = "Stop"` in the runner.
    # Windows PowerShell 5.1 wraps every native-command stderr line in a
    # NativeCommandError record. uvicorn writes its INFO/access logs to
    # stderr, so under "Stop" the runner aborts on the very first
    # "INFO:     Started server process" line and exits with code 1
    # before `*>>` ever flushes anything to the log file. Leaving the
    # default "Continue" preference lets the `*>>` redirect consume all
    # streams normally and the service comes up cleanly.
    $Runner = @"
`$env:MEMORY_WORKSPACE_ID = "$WorkspaceId"
`$env:MEMORY_DB_PATH = "$DbPath"
`$env:VECTOR_DB_PATH = "$VectorPath"
`$env:MEMORY_API_PORT = "$Port"
`$env:MEMORY_FORBID_DEFAULT_WORKSPACE = "1"
`$env:MEMORY_STRICT_WORKSPACE_ISOLATION = "$StrictValue"
`$env:MEMORY_HUB_MODE = "$HubValue"
`$env:OLLAMA_PROBE_SKIP = "1"
Set-Location -LiteralPath "$RepoRoot"
& "$Python" -m agent_memory_lite *>> "$LogPath" 2>&1
"@
    Set-Content -LiteralPath $RunnerPath -Value $Runner -Encoding UTF8

    # -WindowStyle Hidden suppresses the visible console window on
    # the at-logon trigger; -Hidden on the task settings hides it
    # from the foreground task switcher. Together they keep the
    # HTTP service auto-start silent. (The proper fix — pythonw.exe —
    # is used by digest_worker / consolidation; the service task
    # keeps the PowerShell wrapper because it needs env-var setup
    # for the long-running uvicorn process.)
    $ActionArg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunnerPath`""
    $ScheduledAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ActionArg
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -Hidden `
        -ExecutionTimeLimit (New-TimeSpan -Days 0)

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $ScheduledAction `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "agent-memory-lite local memory service for workspace $WorkspaceId" `
        -Force | Out-Null

    Write-JsonStatus @{
        status = "installed"
        task_name = $TaskName
        workspace_id = $WorkspaceId
        project_root = $ProjectRoot
        repo_root = $RepoRoot
        runner_path = $RunnerPath
        log_path = $LogPath
    }
    exit 0
}

if ($Action -eq "Uninstall") {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-JsonStatus @{ status = "uninstalled"; task_name = $TaskName; workspace_id = $WorkspaceId }
    exit 0
}

if ($Action -eq "Start") {
    Start-ScheduledTask -TaskName $TaskName
    Write-JsonStatus @{ status = "started"; task_name = $TaskName; workspace_id = $WorkspaceId }
    exit 0
}

if ($Action -eq "Stop") {
    Stop-ScheduledTask -TaskName $TaskName
    Write-JsonStatus @{ status = "stopped"; task_name = $TaskName; workspace_id = $WorkspaceId }
    exit 0
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $Task) {
    Write-JsonStatus @{ status = "missing"; task_name = $TaskName; workspace_id = $WorkspaceId }
    exit 2
}

$Info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-JsonStatus @{
    status = "present"
    task_name = $TaskName
    workspace_id = $WorkspaceId
    state = [string]$Task.State
    last_run_time = [string]$Info.LastRunTime
    last_task_result = $Info.LastTaskResult
    next_run_time = [string]$Info.NextRunTime
    runner_path = $RunnerPath
    log_path = $LogPath
}
