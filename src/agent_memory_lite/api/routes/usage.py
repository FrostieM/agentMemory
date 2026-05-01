"""POST /memory/record_usage_feedback."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.schemas.usage import (
    RecordUsageFeedbackRequest,
    RecordUsageFeedbackResponse,
)
from agent_memory_lite.maintenance.usage_feedback import record_usage_feedback

router = APIRouter()


@router.post("/memory/record_usage_feedback", response_model=RecordUsageFeedbackResponse)
def record_usage_feedback_route(
    body: RecordUsageFeedbackRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> RecordUsageFeedbackResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    feedback = record_usage_feedback(
        conn,
        workspace_id=body.workspace_id,
        source_type=body.source_type,
        source_id=body.source_id,
        query=body.query,
        usefulness=body.usefulness,
        task_id=body.task_id,
        notes=body.notes,
    )
    return RecordUsageFeedbackResponse.model_validate(feedback.to_dict())
