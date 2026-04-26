"""POST /memory/update_task_state."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep
from agent_memory_lite.api.schemas.task_state import (
    UpdateTaskStateRequest,
    UpdateTaskStateResponse,
)
from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.models.task_state import TaskStateIn

router = APIRouter()


@router.post("/memory/update_task_state", response_model=UpdateTaskStateResponse)
def update_task_state_route(body: UpdateTaskStateRequest, conn: DbDep) -> UpdateTaskStateResponse:
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
    state = write_task_state(conn, payload)
    return UpdateTaskStateResponse(
        state_id=state.id,
        task_id=state.task_id,
        status=state.status,
        updated_at=state.updated_at,
    )
