"""Wire schemas for retrieval usage feedback."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RecordUsageFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    source_type: str = "chunk"
    source_id: str = Field(min_length=1)
    query: str = ""
    usefulness: float = Field(ge=-1.0, le=1.0)
    task_id: str | None = None
    notes: str = ""


class RecordUsageFeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_id: str
    workspace_id: str
    source_type: str
    source_id: str
    query: str
    usefulness: float
    task_id: str | None
    notes: str
    created_at: str
