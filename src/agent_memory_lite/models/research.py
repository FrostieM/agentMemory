"""Research-lab domain models.

These objects organize the scientific workflow around memory:
snapshots provide immutable datasets, experiments test theories against those
snapshots, results update theory confidence, concepts keep vocabulary explicit,
and insights distill episodes into actionable research items.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.models.enums import (
    ConceptKind,
    ExperimentStatus,
    InsightStatus,
    InsightType,
    TheoryEvidenceKind,
)


class MemorySnapshotIn(BaseModel):
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


class MemorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    snapshot_key: str
    title: str
    source: str
    db_path: str | None
    duckdb_path: str | None
    parquet_dir: str | None
    window_start: str | None
    window_end: str | None
    build_sha: str | None
    build_branch: str | None
    build_time: str | None
    remote_host: str | None
    table_counts: dict[str, int]
    total_rows: int
    metadata: dict[str, Any]
    source_episode_id: str | None
    created_at: str
    updated_at: str


class ExperimentIn(BaseModel):
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


class Experiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    theory_id: str | None
    snapshot_id: str | None
    title: str
    hypothesis: str
    cohort_definition: str | None
    success_criteria: dict[str, Any]
    command: str | None
    status: ExperimentStatus
    priority: float
    owner: str | None
    due_at: str | None
    source_episode_id: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    completed_at: str | None


class ExperimentResultIn(BaseModel):
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


class ExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
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


class DomainConceptIn(BaseModel):
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


class DomainConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    name: str
    kind: ConceptKind
    definition: str
    aliases: list[str]
    tags: list[str]
    source_episode_id: str | None
    confidence: float
    active: bool
    created_at: str
    updated_at: str


class ResearchInsightIn(BaseModel):
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


class ResearchInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    workspace_id: str
    insight_type: InsightType
    summary: str
    proposed_action: str | None
    target_type: str | None
    target_id: str | None
    source_episode_ids: list[str]
    confidence: float
    status: InsightStatus
    tags: list[str]
    created_at: str
    updated_at: str


class ResearchAgenda(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[MemorySnapshot] = Field(default_factory=list)
    experiments: list[Experiment] = Field(default_factory=list)
    insights: list[ResearchInsight] = Field(default_factory=list)
    concepts: list[DomainConcept] = Field(default_factory=list)
