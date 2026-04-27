"""Theory domain models.

Theories are mutable research objects: a claim about how a project works, why
an edge might exist, or what should be tested next. They are deliberately not
decisions: a theory can be proposed, tested, weakened, supported, or rejected as
evidence accumulates.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import TheoryEvidenceKind, TheoryStatus


class TheoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    title: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    domain: str = Field(default="general", min_length=1)
    mechanism: str | None = None
    predictions: list[str] = Field(default_factory=list)
    validation_criteria: list[str] = Field(default_factory=list)
    experiment_plan: str | None = None
    dependent_decision_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: TheoryStatus = TheoryStatus.PROPOSED
    supersedes_theory_id: str | None = None
    source_episode_id: str | None = None
    confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)


class Theory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    title: str
    domain: str
    claim: str
    mechanism: str | None
    predictions: list[str]
    validation_criteria: list[str]
    experiment_plan: str | None
    dependent_decision_ids: list[str]
    tags: list[str]
    status: TheoryStatus
    supersedes_theory_id: str | None
    source_episode_id: str | None
    confidence: float
    importance: float
    evidence_count: int
    evidence_strength: float
    created_at: str
    updated_at: str
    last_tested_at: str | None


class TheoryEvidenceIn(BaseModel):
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


class TheoryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
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
