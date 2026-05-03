"""Wire schemas for /memory/insight_candidates routes (v1.8)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InsightCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    insight_type: str
    summary: str
    proposed_action: str | None
    target_type: str | None
    target_id: str | None
    source_episode_ids: list[str]
    confidence: float
    status: str
    promoted_insight_id: str | None
    tags: list[str]
    created_at: str
    updated_at: str
    decided_at: str | None
    decided_by: str | None


class ListInsightCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    candidates: list[InsightCandidateResponse]


class InsightCandidateAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    decided_by: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    summary_override: str | None = Field(default=None, min_length=1)
    proposed_action_override: str | None = None


class InsightCandidateRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    decided_by: str | None = None
    reason: str | None = None


class InsightCandidateAcceptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    insight_id: str
    status: str


class InsightCandidateRejectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    status: str
