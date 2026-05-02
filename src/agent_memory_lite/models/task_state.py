"""Task state: per-(workspace, task_id) progress snapshot."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TaskStateIn(BaseModel):
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


class TaskState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    task_id: str
    goal: str
    status: str
    current_plan: list[str]
    completed_steps: list[str]
    next_action: str | None
    blockers: list[str]
    files_in_scope: list[str]
    source_episode_id: str | None
    updated_at: str
