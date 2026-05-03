"""Wire schemas for /memory/decision_candidates routes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DecisionCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    theory_id: str
    proposed_title: str
    proposed_decision_text: str
    proposed_rationale: str | None
    evidence_count: int
    evidence_strength: float
    confidence: float
    status: str
    promoted_decision_id: str | None
    created_at: str
    updated_at: str
    decided_at: str | None
    decided_by: str | None


class ListDecisionCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    candidates: list[DecisionCandidateResponse]


class PromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    decided_by: str | None = None
    title_override: str | None = Field(default=None, min_length=1)
    decision_text_override: str | None = Field(default=None, min_length=1)
    rationale_override: str | None = None


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    decided_by: str | None = None
    reason: str | None = None


class PromoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    decision_id: str
    status: str


class RejectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    status: str
