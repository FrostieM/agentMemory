"""Wire-side schemas for `memory_update_task_state`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UpdateTaskStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    task_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    status: str = Field(min_length=1)
    current_plan: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    next_action: str | None = None
    blockers: list[str] = Field(default_factory=list)
    files_in_scope: list[str] = Field(default_factory=list)
    source_episode_id: str | None = None


class UpdateTaskStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_id: str
    task_id: str
    status: str
    updated_at: str
