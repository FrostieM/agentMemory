"""Research-lab memory routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_allowed
from agent_memory_lite.api.schemas.research import (
    AddExperimentResultRequest,
    ConceptResponse,
    DistillInsightRequest,
    ExperimentResponse,
    ExperimentResultResponse,
    InsightResponse,
    ListConceptsRequest,
    ListConceptsResponse,
    ListInsightsRequest,
    ListInsightsResponse,
    ListResearchAgendaRequest,
    MemorySnapshotResponse,
    RegisterSnapshotRequest,
    ResearchAgendaResponse,
    UpsertConceptRequest,
    WriteExperimentRequest,
)
from agent_memory_lite.ingestion.research_writer import (
    add_experiment_result,
    distill_insight,
    register_snapshot,
    upsert_domain_concept,
    write_experiment,
)
from agent_memory_lite.models.research import (
    DomainConcept,
    DomainConceptIn,
    Experiment,
    ExperimentIn,
    ExperimentResult,
    ExperimentResultIn,
    MemorySnapshot,
    MemorySnapshotIn,
    ResearchInsight,
    ResearchInsightIn,
)
from agent_memory_lite.repositories.research_repo import (
    build_research_agenda,
    list_concepts,
    list_insights,
)

router = APIRouter()


def _snapshot_response(snapshot: MemorySnapshot) -> MemorySnapshotResponse:
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


def _experiment_response(experiment: Experiment) -> ExperimentResponse:
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


def _result_response(result: ExperimentResult) -> ExperimentResultResponse:
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


def _concept_response(concept: DomainConcept) -> ConceptResponse:
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


def _insight_response(insight: ResearchInsight) -> InsightResponse:
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


@router.post("/memory/register_snapshot", response_model=MemorySnapshotResponse)
def register_snapshot_route(
    body: RegisterSnapshotRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> MemorySnapshotResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    snapshot = register_snapshot(
        conn,
        MemorySnapshotIn(
            workspace_id=body.workspace_id,
            snapshot_key=body.snapshot_key,
            title=body.title,
            source=body.source,
            db_path=body.db_path,
            duckdb_path=body.duckdb_path,
            parquet_dir=body.parquet_dir,
            window_start=body.window_start,
            window_end=body.window_end,
            build_sha=body.build_sha,
            build_branch=body.build_branch,
            build_time=body.build_time,
            remote_host=body.remote_host,
            table_counts=body.table_counts,
            total_rows=body.total_rows,
            metadata=body.metadata,
            source_episode_id=body.source_episode_id,
        ),
    )
    return _snapshot_response(snapshot)


@router.post("/memory/write_experiment", response_model=ExperimentResponse)
def write_experiment_route(
    body: WriteExperimentRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ExperimentResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    experiment = write_experiment(
        conn,
        ExperimentIn(
            workspace_id=body.workspace_id,
            theory_id=body.theory_id,
            snapshot_id=body.snapshot_id,
            title=body.title,
            hypothesis=body.hypothesis,
            cohort_definition=body.cohort_definition,
            success_criteria=body.success_criteria,
            command=body.command,
            status=body.status,
            priority=body.priority,
            owner=body.owner,
            due_at=body.due_at,
            source_episode_id=body.source_episode_id,
            metadata=body.metadata,
        ),
    )
    return _experiment_response(experiment)


@router.post("/memory/add_experiment_result", response_model=ExperimentResultResponse)
def add_experiment_result_route(
    body: AddExperimentResultRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ExperimentResultResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    result = add_experiment_result(
        conn,
        ExperimentResultIn(
            workspace_id=body.workspace_id,
            experiment_id=body.experiment_id,
            theory_id=body.theory_id,
            kind=body.kind,
            summary=body.summary,
            metrics=body.metrics,
            artifact_path=body.artifact_path,
            confidence=body.confidence,
            observed_at=body.observed_at,
            source_episode_id=body.source_episode_id,
        ),
    )
    return _result_response(result)


@router.post("/memory/upsert_concept", response_model=ConceptResponse)
def upsert_concept_route(
    body: UpsertConceptRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ConceptResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    concept = upsert_domain_concept(
        conn,
        DomainConceptIn(
            workspace_id=body.workspace_id,
            name=body.name,
            kind=body.kind,
            definition=body.definition,
            aliases=body.aliases,
            tags=body.tags,
            source_episode_id=body.source_episode_id,
            confidence=body.confidence,
            active=body.active,
        ),
    )
    return _concept_response(concept)


@router.post("/memory/distill_insight", response_model=InsightResponse)
def distill_insight_route(
    body: DistillInsightRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> InsightResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    insight = distill_insight(
        conn,
        ResearchInsightIn(
            workspace_id=body.workspace_id,
            insight_type=body.insight_type,
            summary=body.summary,
            proposed_action=body.proposed_action,
            target_type=body.target_type,
            target_id=body.target_id,
            source_episode_ids=body.source_episode_ids,
            confidence=body.confidence,
            status=body.status,
            tags=body.tags,
        ),
    )
    return _insight_response(insight)


@router.post("/memory/list_research_agenda", response_model=ResearchAgendaResponse)
def list_research_agenda_route(
    body: ListResearchAgendaRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ResearchAgendaResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    agenda = build_research_agenda(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        limit=body.limit,
    )
    return ResearchAgendaResponse(
        snapshots=[_snapshot_response(item) for item in agenda.snapshots],
        experiments=[_experiment_response(item) for item in agenda.experiments],
        insights=[_insight_response(item) for item in agenda.insights],
        concepts=[_concept_response(item) for item in agenda.concepts],
    )


@router.post("/memory/list_concepts", response_model=ListConceptsResponse)
def list_concepts_route(
    body: ListConceptsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListConceptsResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    concepts = list_concepts(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        include_inactive=body.include_inactive,
        limit=body.limit,
    )
    return ListConceptsResponse(concepts=[_concept_response(item) for item in concepts])


@router.post("/memory/list_insights", response_model=ListInsightsResponse)
def list_insights_route(
    body: ListInsightsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListInsightsResponse:
    ensure_workspace_allowed(body.workspace_id, settings)
    insights = list_insights(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        statuses=body.statuses,
        limit=body.limit,
    )
    return ListInsightsResponse(insights=[_insight_response(item) for item in insights])
