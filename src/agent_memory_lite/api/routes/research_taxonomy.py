"""Concept + insight list routes (taxonomy surface).

Insight write routes live in ``research_insights.py`` and are mounted
on the same prefix from this module.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_readable,
    ensure_workspace_writable,
)
from agent_memory_lite.api.routes.research_insights import router as insights_router
from agent_memory_lite.api.routes.research_responses import (
    to_concept_response,
    to_insight_response,
)
from agent_memory_lite.api.schemas.research import (
    ConceptResponse,
    ListConceptsRequest,
    ListConceptsResponse,
    ListInsightsRequest,
    ListInsightsResponse,
    UpsertConceptRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.ingestion.research_writer import upsert_domain_concept
from agent_memory_lite.models.research import DomainConceptIn
from agent_memory_lite.repositories.research_repo import list_concepts, list_insights

router = APIRouter()
router.include_router(insights_router)


@router.post("/memory/upsert_concept", response_model=ConceptResponse)
def upsert_concept_route(
    body: UpsertConceptRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ConceptResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    with trace_memory_operation(
        workspace_id=body.workspace_id,
        endpoint="/memory/upsert_concept",
        operation="upsert_concept",
        label="Upsert concept",
        snippet=body.name,
    ) as trace:
        trace.stage_done("validate", "Concept payload accepted", snippet=body.name)
        trace.stage_started("persist", "Persist concept")
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
        trace.stage_done("persist", "Concept persisted", counts={"active": concept.active})
        trace.graph_delta(
            object_type="concept",
            object_id=concept.id,
            action="upserted",
            label="Concept updated",
        )
        response = to_concept_response(concept)
        trace.stage_done("response", "Concept response ready", counts={"concept_id": concept.id})
        return response


@router.post("/memory/list_concepts", response_model=ListConceptsResponse)
def list_concepts_route(
    body: ListConceptsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListConceptsResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    concepts = list_concepts(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        include_inactive=body.include_inactive,
        limit=body.limit,
    )
    return ListConceptsResponse(concepts=[to_concept_response(item) for item in concepts])


@router.post("/memory/list_insights", response_model=ListInsightsResponse)
def list_insights_route(
    body: ListInsightsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ListInsightsResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    insights = list_insights(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        statuses=body.statuses,
        limit=body.limit,
    )
    return ListInsightsResponse(insights=[to_insight_response(item) for item in insights])
