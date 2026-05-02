"""Wire-shape converters for research routes.

Split out of ``research.py`` so each module stays under the SLOC
ceiling. The five helpers below copy domain models into their wire
schemas; reused by the snapshot, experiment, concept, insight, and
agenda endpoints.
"""

from __future__ import annotations

from agent_memory_lite.api.schemas.research import (
    ConceptResponse,
    ExperimentResponse,
    ExperimentResultResponse,
    InsightResponse,
    MemorySnapshotResponse,
)
from agent_memory_lite.models.research import (
    DomainConcept,
    Experiment,
    ExperimentResult,
    MemorySnapshot,
    ResearchInsight,
)


def to_snapshot_response(snapshot: MemorySnapshot) -> MemorySnapshotResponse:
    return MemorySnapshotResponse(
        snapshot_id=snapshot.id,
        workspace_id=snapshot.workspace_id,
        snapshot_key=snapshot.snapshot_key,
        title=snapshot.title,
        source=snapshot.source,
        db_path=snapshot.db_path,
        duckdb_path=snapshot.duckdb_path,
        parquet_dir=snapshot.parquet_dir,
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        build_sha=snapshot.build_sha,
        build_branch=snapshot.build_branch,
        build_time=snapshot.build_time,
        remote_host=snapshot.remote_host,
        table_counts=snapshot.table_counts,
        total_rows=snapshot.total_rows,
        metadata=snapshot.metadata,
        source_episode_id=snapshot.source_episode_id,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


def to_experiment_response(experiment: Experiment) -> ExperimentResponse:
    return ExperimentResponse(
        experiment_id=experiment.id,
        workspace_id=experiment.workspace_id,
        theory_id=experiment.theory_id,
        snapshot_id=experiment.snapshot_id,
        title=experiment.title,
        hypothesis=experiment.hypothesis,
        cohort_definition=experiment.cohort_definition,
        success_criteria=experiment.success_criteria,
        command=experiment.command,
        status=experiment.status,
        priority=experiment.priority,
        owner=experiment.owner,
        due_at=experiment.due_at,
        source_episode_id=experiment.source_episode_id,
        metadata=experiment.metadata,
        created_at=experiment.created_at,
        updated_at=experiment.updated_at,
        completed_at=experiment.completed_at,
    )


def to_result_response(result: ExperimentResult) -> ExperimentResultResponse:
    return ExperimentResultResponse(
        result_id=result.id,
        workspace_id=result.workspace_id,
        experiment_id=result.experiment_id,
        theory_id=result.theory_id,
        kind=result.kind,
        summary=result.summary,
        metrics=result.metrics,
        artifact_path=result.artifact_path,
        confidence=result.confidence,
        observed_at=result.observed_at,
        source_episode_id=result.source_episode_id,
        created_at=result.created_at,
    )


def to_concept_response(concept: DomainConcept) -> ConceptResponse:
    return ConceptResponse(
        concept_id=concept.id,
        workspace_id=concept.workspace_id,
        name=concept.name,
        kind=concept.kind,
        definition=concept.definition,
        aliases=concept.aliases,
        tags=concept.tags,
        source_episode_id=concept.source_episode_id,
        confidence=concept.confidence,
        active=concept.active,
        created_at=concept.created_at,
        updated_at=concept.updated_at,
    )


def to_insight_response(insight: ResearchInsight) -> InsightResponse:
    return InsightResponse(
        insight_id=insight.id,
        workspace_id=insight.workspace_id,
        insight_type=insight.insight_type,
        summary=insight.summary,
        proposed_action=insight.proposed_action,
        target_type=insight.target_type,
        target_id=insight.target_id,
        source_episode_ids=insight.source_episode_ids,
        confidence=insight.confidence,
        status=insight.status,
        tags=insight.tags,
        created_at=insight.created_at,
        updated_at=insight.updated_at,
    )
