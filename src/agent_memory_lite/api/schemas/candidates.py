"""Wire schemas for reviewable memory candidates."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import MemoryCandidateKind, MemoryCandidateStatus, TrustLevel


class CandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    workspace_id: str
    kind: MemoryCandidateKind
    subject: str
    predicate: str
    object: str | None
    evidence: str
    confidence: float
    importance: float
    trust_level: TrustLevel
    temporal: dict[str, Any]
    write_targets: list[str]
    metadata: dict[str, Any]
    source_episode_id: str
    status: MemoryCandidateStatus
    promoted_target_type: str | None
    promoted_target_id: str | None
    created_at: str
    updated_at: str
    decided_at: str | None


class ListCandidatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    statuses: list[MemoryCandidateStatus] | None = None
    limit: int = Field(default=20, ge=1, le=100)
    since: str | None = None
    until: str | None = None


class ListCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateResponse]


class CandidateActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    # Round-5 audit: promote / reject WRITE. workspace_id makes the
    # action target guardable — None resolves to the service anchor, an
    # explicit foreign value is blocked under strict isolation. Optional
    # so existing callers stay wire-compatible.
    workspace_id: str | None = None
