"""Wire-side schemas for research-lab memory."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_memory_lite.models.enums import (
    ConceptKind,
    ExperimentStatus,
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
)


class RegisterSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    snapshot_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source: str = Field(default="manual", min_length=1)
    db_path: str | None = None
    duckdb_path: str | None = None
    parquet_dir: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    build_sha: str | None = None
    build_branch: str | None = None
    build_time: str | None = None
    remote_host: str | None = None
    table_counts: dict[str, int] = Field(default_factory=dict)
    total_rows: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_episode_id: str | None = None


class MemorySnapshotResponse(RegisterSnapshotRequest):
    snapshot_id: str
    created_at: str
    updated_at: str


class WriteExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    theory_id: str | None = None
    snapshot_id: str | None = None
    title: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    cohort_definition: str | None = None
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    command: str | None = None
    status: ExperimentStatus = ExperimentStatus.PLANNED
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    owner: str | None = None
    due_at: str | None = None
    source_episode_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentResponse(WriteExperimentRequest):
    experiment_id: str
    created_at: str
    updated_at: str
    completed_at: str | None


class AddExperimentResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    experiment_id: str = Field(min_length=1)
    theory_id: str | None = None
    kind: TheoryEvidenceKind
    summary: str = Field(min_length=1)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_path: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    observed_at: str | None = None
    source_episode_id: str | None = None


class ExperimentResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    workspace_id: str
    experiment_id: str
    theory_id: str | None
    kind: TheoryEvidenceKind
    summary: str
    metrics: dict[str, Any]
    artifact_path: str | None
    confidence: float
    observed_at: str
    source_episode_id: str | None
    created_at: str


class UpsertConceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    name: str = Field(min_length=1)
    kind: ConceptKind = ConceptKind.TERM
    definition: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_episode_id: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    active: bool = True


class ConceptResponse(UpsertConceptRequest):
    concept_id: str
    created_at: str
    updated_at: str


class DistillInsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    insight_type: InsightType
    summary: str = Field(min_length=1)
    proposed_action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    source_episode_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    status: InsightStatus = InsightStatus.NEW
    tags: list[str] = Field(default_factory=list)


class InsightResponse(DistillInsightRequest):
    insight_id: str
    created_at: str
    updated_at: str


class UpdateInsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    insight_id: str = Field(min_length=1)
    target_type: str | None = Field(default=None, min_length=1)
    target_id: str | None = Field(default=None, min_length=1)
    status: InsightStatus | None = None
    source_episode_id: str | None = None

    @model_validator(mode="after")
    def require_update(self) -> UpdateInsightRequest:
        has_target_type = bool(self.target_type)
        has_target_id = bool(self.target_id)
        if has_target_type != has_target_id:
            raise ValueError("target_type and target_id must be provided together")
        if not has_target_type and self.status is None:
            raise ValueError("provide a target link or status update")
        return self


class ListResearchAgendaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class ResearchAgendaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[MemorySnapshotResponse] = Field(default_factory=list)
    experiments: list[ExperimentResponse] = Field(default_factory=list)
    insights: list[InsightResponse] = Field(default_factory=list)
    concepts: list[ConceptResponse] = Field(default_factory=list)


class ListConceptsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    include_inactive: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class ListConceptsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concepts: list[ConceptResponse]


class ListInsightsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    statuses: list[InsightStatus] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ListInsightsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insights: list[InsightResponse]
