"""Experiment + experiment_result routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.routes.research_responses import (
    to_experiment_response,
    to_result_response,
)
from agent_memory_lite.api.schemas.research import (
    AddExperimentResultRequest,
    ExperimentResponse,
    ExperimentResultResponse,
    WriteExperimentRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.ingestion.research_writer import (
    add_experiment_result,
    write_experiment,
)
from agent_memory_lite.models.research import ExperimentIn, ExperimentResultIn

router = APIRouter()


@router.post("/memory/write_experiment", response_model=ExperimentResponse)
def write_experiment_route(
    body: WriteExperimentRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ExperimentResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    ensure_workspace_matches_db(conn, body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/write_experiment",
        operation="write_experiment",
        label="Write experiment",
        snippet=body.title,
    ) as trace:
        trace.stage_done("validate", "Experiment payload accepted", snippet=body.title)
        trace.stage_started("persist", "Persist experiment")
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
        trace.stage_done("persist", "Experiment persisted", counts={"status": experiment.status})
        trace.graph_delta(
            object_type="experiment",
            object_id=experiment.id,
            action="created",
            label="Experiment written",
        )
        response = to_experiment_response(experiment)
        trace.stage_done(
            "response", "Experiment response ready", counts={"experiment_id": experiment.id}
        )
        return response


@router.post("/memory/add_experiment_result", response_model=ExperimentResultResponse)
def add_experiment_result_route(
    body: AddExperimentResultRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ExperimentResultResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    ensure_workspace_matches_db(conn, body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/add_experiment_result",
        operation="add_experiment_result",
        label="Add experiment result",
        snippet=body.summary,
    ) as trace:
        trace.stage_done("validate", "Experiment result payload accepted", snippet=body.summary)
        trace.stage_started("persist", "Persist experiment result")
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
        trace.stage_done("persist", "Experiment result persisted", counts={"kind": result.kind})
        trace.graph_delta(
            object_type="experiment_result",
            object_id=result.id,
            action="created",
            label="Experiment result added",
            counts={"experiment_id": result.experiment_id},
        )
        response = to_result_response(result)
        trace.stage_done(
            "response", "Experiment result response ready", counts={"result_id": result.id}
        )
        return response
