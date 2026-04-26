"""Decision domain models.

A decision is an explicit architectural choice. New decisions can supersede an
older one, in which case the prior decision's `valid_to` is closed and its
`status` flips to `superseded`. Both stay in the table for historical queries.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import DecisionStatus


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    title: str = Field(min_length=1)
    decision_text: str = Field(min_length=1)
    rationale: str | None = None
    supersedes_decision_id: str | None = None
    source_episode_id: str | None = None
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    importance: float = Field(default=0.8, ge=0.0, le=1.0)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    title: str
    decision_text: str
    rationale: str | None
    status: DecisionStatus
    supersedes_decision_id: str | None
    source_episode_id: str | None
    confidence: float
    importance: float
    valid_from: str
    valid_to: str | None
    created_at: str
    updated_at: str
