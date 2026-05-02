"""Research-lab memory routes (agenda).

Snapshot / experiment / result routes live in ``research_snapshots.py``;
concept / insight routes live in ``research_taxonomy.py``. Both child
routers are mounted on the main router below so the public surface
stays identical.
"""

from __future__ import annotations

from fastapi import APIRouter

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.research_responses import (
    to_concept_response,
    to_experiment_response,
    to_insight_response,
    to_snapshot_response,
)
from agent_memory_lite.api.routes.research_snapshots import router as snapshots_router
from agent_memory_lite.api.routes.research_taxonomy import router as taxonomy_router
from agent_memory_lite.api.schemas.research import (
    ListResearchAgendaRequest,
    ResearchAgendaResponse,
)
from agent_memory_lite.repositories.research_repo import build_research_agenda

router = APIRouter()
router.include_router(snapshots_router)
router.include_router(taxonomy_router)


@router.post("/memory/list_research_agenda", response_model=ResearchAgendaResponse)
def list_research_agenda_route(
    body: ListResearchAgendaRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ResearchAgendaResponse:
    ensure_workspace_readable(body.workspace_id, settings)
    agenda = build_research_agenda(
        conn,
        workspace_id=body.workspace_id,
        query=body.query,
        limit=body.limit,
        since=body.since,
        until=body.until,
    )
    return ResearchAgendaResponse(
        snapshots=[to_snapshot_response(item) for item in agenda.snapshots],
        experiments=[to_experiment_response(item) for item in agenda.experiments],
        insights=[to_insight_response(item) for item in agenda.insights],
        concepts=[to_concept_response(item) for item in agenda.concepts],
    )
