"""Concept write routes.

Insight write routes live in ``insights.py`` and are mounted
on the same prefix from this module.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import (
    DbDep,
    SettingsDep,
    ensure_workspace_writable,
)
from agent_memory_lite.api.routes.research_insights import router as insights_router
from agent_memory_lite.api.routes.research_responses import (
    to_concept_response,
)
from agent_memory_lite.api.schemas.research import (
    ConceptResponse,
    UpsertConceptRequest,
)
from agent_memory_lite.api.ui_telemetry import trace_memory_operation
from agent_memory_lite.api.workspace_routing import ensure_workspace_matches_db
from agent_memory_lite.ingestion.research_writer import upsert_domain_concept
from agent_memory_lite.models.research import DomainConceptIn

router = APIRouter()
router.include_router(insights_router)


@router.post("/memory/upsert_concept", response_model=ConceptResponse)
def upsert_concept_route(
    body: UpsertConceptRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ConceptResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    ensure_workspace_matches_db(conn, body.workspace_id, settings)
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
