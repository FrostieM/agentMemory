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


class ListDecisionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    include_superseded: bool = False
    limit: int = Field(default=10, ge=1, le=100)


class DecisionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    title: str
    decision_text: str
    rationale: str | None
    status: str
    supersedes_decision_id: str | None
    source_episode_id: str | None
    confidence: float
    importance: float
    valid_from: str
    valid_to: str | None
    created_at: str
    updated_at: str


class ListDecisionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[DecisionItem]
