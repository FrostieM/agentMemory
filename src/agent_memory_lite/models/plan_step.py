"""Plan step: one first-class, ordered step of a task's plan.

Replaces the flat ``current_plan`` / ``completed_steps`` string lists on
``task_state`` — a step is now a row with identity, status, order, and
bi-temporal validity.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlanStepStatus = Literal["pending", "active", "done", "blocked", "skipped"]


class PlanStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str = ""
    status: PlanStepStatus = "pending"
    parent_step_id: str | None = None
    rank: float | None = None  # None -> writer appends after the last step
    supersedes_step_id: str | None = None
    source_episode_id: str | None = None


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    task_id: str
    title: str
    body: str
    status: PlanStepStatus
    parent_step_id: str | None
    rank: float
    supersedes_step_id: str | None
    source_episode_id: str | None
    valid_from: str
    valid_to: str | None
    created_at: str
    updated_at: str
