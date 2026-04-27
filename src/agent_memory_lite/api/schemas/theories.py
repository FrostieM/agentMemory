"""Wire-side schemas for theory memory."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import TheoryEvidenceKind, TheoryStatus


class WriteTheoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    title: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    domain: str = Field(default="general", min_length=1)
    mechanism: str | None = None
    predictions: list[str] = Field(default_factory=list)
    experiment_plan: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: TheoryStatus = TheoryStatus.PROPOSED
    supersedes_theory_id: str | None = None
    source_episode_id: str | None = None
    confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)


class TheoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theory_id: str
    workspace_id: str
    title: str
    domain: str
    claim: str
    mechanism: str | None
    predictions: list[str]
    experiment_plan: str | None
    tags: list[str]
    status: TheoryStatus
    supersedes_theory_id: str | None
    source_episode_id: str | None
    confidence: float
    importance: float
    created_at: str
    updated_at: str
    last_tested_at: str | None


class AddTheoryEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    theory_id: str = Field(min_length=1)
    kind: TheoryEvidenceKind
    summary: str = Field(min_length=1)
    source_episode_id: str | None = None
    artifact_path: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    observed_at: str | None = None


class TheoryEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    workspace_id: str
    theory_id: str
    kind: TheoryEvidenceKind
    summary: str
    source_episode_id: str | None
    artifact_path: str | None
    metrics: dict[str, Any]
    confidence: float
    observed_at: str
    created_at: str


class ListTheoriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    statuses: list[TheoryStatus] | None = None
    include_archived: bool = False
    include_evidence: bool = False
    evidence_limit: int = Field(default=3, ge=0, le=20)
    limit: int = Field(default=20, ge=1, le=100)


class TheoryWithEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theory: TheoryResponse
    evidence: list[TheoryEvidenceResponse] = Field(default_factory=list)


class ListTheoriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theories: list[TheoryWithEvidenceResponse]
