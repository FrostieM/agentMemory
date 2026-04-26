"""Wire-side schemas for `memory_write_decision`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WriteDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    title: str = Field(min_length=1)
    decision_text: str = Field(min_length=1)
    rationale: str | None = None
    supersedes_decision_id: str | None = None
    source_episode_id: str | None = None
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    importance: float = Field(default=0.8, ge=0.0, le=1.0)


class WriteDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: str
    valid_from: str
    superseded_decision_id: str | None
