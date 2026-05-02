"""Wire-side schemas for research-lab memory.

This module holds snapshots, experiments, experiment results, and the
research-agenda response. Concept and insight schemas live in
``research_taxonomy.py``; they are re-exported here so existing callers
that imported them from ``research`` keep working.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.schemas.research_taxonomy import (
    ConceptResponse,
    DistillInsightRequest,
    InsightResponse,
    ListConceptsRequest,
    ListConceptsResponse,
    ListInsightsRequest,
    ListInsightsResponse,
    UpdateInsightRequest,
    UpsertConceptRequest,
)
from agent_memory_lite.models.enums import ExperimentStatus, TheoryEvidenceKind

__all__ = [
    "AddExperimentResultRequest",
    # Re-exports from research_taxonomy:
    "ConceptResponse",
    "DistillInsightRequest",
    "ExperimentResponse",
    "ExperimentResultResponse",
    "InsightResponse",
    "ListConceptsRequest",
    "ListConceptsResponse",
    "ListInsightsRequest",
    "ListInsightsResponse",
    "ListResearchAgendaRequest",
    "MemorySnapshotResponse",
    "RegisterSnapshotRequest",
    "ResearchAgendaResponse",
    "UpdateInsightRequest",
    "UpsertConceptRequest",
    "WriteExperimentRequest",
]


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


class ListResearchAgendaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=50)
    since: str | None = None
    until: str | None = None


class ResearchAgendaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[MemorySnapshotResponse] = Field(default_factory=list)
    experiments: list[ExperimentResponse] = Field(default_factory=list)
    insights: list[InsightResponse] = Field(default_factory=list)
    concepts: list[ConceptResponse] = Field(default_factory=list)
