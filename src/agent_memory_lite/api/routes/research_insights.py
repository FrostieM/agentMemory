"""Insight write/update routes."""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.routes.research_responses import to_insight_response
from agent_memory_lite.api.schemas.research import (
    DistillInsightRequest,
    InsightResponse,
    UpdateInsightRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.ingestion.research_writer import distill_insight, update_insight
from agent_memory_lite.models.research import (
    ResearchInsightIn,
    ResearchInsightUpdateIn,
)

router = APIRouter()


@router.post("/memory/distill_insight", response_model=InsightResponse)
def distill_insight_route(
    body: DistillInsightRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> InsightResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    ensure_workspace_matches_db(conn, body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/distill_insight",
        operation="distill_insight",
        label="Distill insight",
        snippet=body.summary,
    ) as trace:
        trace.stage_done("validate", "Insight payload accepted", snippet=body.summary)
        trace.stage_started("persist", "Persist insight")
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
        trace.stage_done("persist", "Insight persisted", counts={"status": insight.status})
        trace.graph_delta(
            object_type="insight",
            object_id=insight.id,
            action="created",
            label="Insight distilled",
        )
        response = to_insight_response(insight)
        trace.stage_done("response", "Insight response ready", counts={"insight_id": insight.id})
        return response


@router.post("/memory/update_insight", response_model=InsightResponse)
def update_insight_route(
    body: UpdateInsightRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> InsightResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    ensure_workspace_matches_db(conn, body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/update_insight",
        operation="update_insight",
        label="Update insight",
        snippet=body.insight_id,
    ) as trace:
        trace.stage_done("validate", "Insight update accepted", snippet=body.insight_id)
        trace.stage_started("persist", "Persist insight update")
        insight = update_insight(
            conn,
            ResearchInsightUpdateIn(
                workspace_id=body.workspace_id,
                insight_id=body.insight_id,
                target_type=body.target_type,
                target_id=body.target_id,
                status=body.status,
                source_episode_id=body.source_episode_id,
            ),
        )
        trace.stage_done("persist", "Insight updated", counts={"status": insight.status})
        trace.graph_delta(
            object_type="insight",
            object_id=insight.id,
            action="updated",
            label="Insight updated",
        )
        response = to_insight_response(insight)
        trace.stage_done(
            "response", "Insight update response ready", counts={"insight_id": insight.id}
        )
        return response
