param(
    [string]$WorkspaceId = $env:MEMORY_WORKSPACE_ID,
    [string]$TaskName = "",
    [string]$Uri = "http://127.0.0.1:8765/health",
    [switch]$RestartTask
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-JsonStatus([hashtable]$Payload) {
    $Payload | ConvertTo-Json -Depth 8
}

if ([string]::IsNullOrWhiteSpace($WorkspaceId)) {
    throw "WorkspaceId is required. Pass -WorkspaceId or set MEMORY_WORKSPACE_ID."
}
if ([string]::IsNullOrWhiteSpace($TaskName)) {
    $TaskName = "agent-memory-lite-$WorkspaceId"
}

try {
    $Health = Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 5
    $HealthWorkspace = [string]$Health.workspace_id
    $RetrievalStatus = [string]$Health.retrieval_integrity.status
    $Ok = ($Health.status -eq "ok") -and ($HealthWorkspace -eq $WorkspaceId)
    $Status = if ($Ok) { "ok" } else { "degraded" }
    Write-JsonStatus @{
        status = $Status
        workspace_id = $WorkspaceId
        service_workspace_id = $HealthWorkspace
        health_status = [string]$Health.status
        retrieval_integrity = $RetrievalStatus
        uri = $Uri
    }
    if ($Ok) { exit 0 }
    exit 2
}
catch {
    if ($RestartTask) {
        Start-ScheduledTask -TaskName $TaskName
        Write-JsonStatus @{
            status = "restarted_task"
            workspace_id = $WorkspaceId
            task_name = $TaskName
            uri = $Uri
            error = $_.Exception.Message
        }
        exit 2
    }

    Write-JsonStatus @{
        status = "unreachable"
        workspace_id = $WorkspaceId
        task_name = $TaskName
        uri = $Uri
        error = $_.Exception.Message
    }
    exit 2
}
