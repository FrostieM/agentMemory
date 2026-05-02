"""POST /memory/update_task_state."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.schemas.task_state import (
    UpdateTaskStateRequest,
    UpdateTaskStateResponse,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.models.task_state import TaskStateIn

router = APIRouter()


@router.post("/memory/update_task_state", response_model=UpdateTaskStateResponse)
def update_task_state_route(
    body: UpdateTaskStateRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> UpdateTaskStateResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/update_task_state",
        operation="update_task_state",
        label="Update task state",
        snippet=body.goal,
    ) as trace:
        trace.stage_done(
            "validate",
            "Task state payload accepted",
            counts={"status": body.status, "files_in_scope": len(body.files_in_scope)},
            snippet=body.task_id,
        )
        payload = TaskStateIn(
            workspace_id=body.workspace_id,
            task_id=body.task_id,
            goal=body.goal,
            status=body.status,
            current_plan=body.current_plan,
            completed_steps=body.completed_steps,
            next_action=body.next_action,
            blockers=body.blockers,
            files_in_scope=body.files_in_scope,
            source_episode_id=body.source_episode_id,
        )
        trace.stage_started("persist", "Persist task state")
        state = write_task_state(conn, payload)
        trace.stage_done("persist", "Task state persisted", counts={"status": state.status})
        trace.graph_delta(
            object_type="task_state",
            object_id=state.id,
            action="updated",
            label="Task state updated",
            counts={"task_id": state.task_id},
        )
        response = UpdateTaskStateResponse(
            state_id=state.id,
            task_id=state.task_id,
            status=state.status,
            updated_at=state.updated_at,
        )
        trace.stage_done("response", "Task state response ready", counts={"state_id": state.id})
        return response
