"""1.7.0: POST /memory/soft_neighbors — heuristic graph neighbors.

The hard graph (``/memory/graph_neighbors``) records EXPLICIT
relationships from the AST. The soft graph captures HEURISTIC ones:
``co_changed`` (these symbols evolve together), ``co_referenced``,
``similar_signature``. Returns weighted neighbors so callers can
threshold on ``weight`` for "frequently together" vs "occasionally
together".
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.models.soft_edges import ALLOWED_SOFT_KINDS, SoftEdge
from agent_memory_lite.repositories.soft_edges_repo import list_soft_neighbors

router = APIRouter()


class SoftNeighborsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    src_qualified_name: str = Field(min_length=1, max_length=400)
    edge_kinds: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=200)


class SoftNeighborsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    src_qualified_name: str
    total: int
    neighbors: list[SoftEdge]


@router.post("/memory/soft_neighbors", response_model=SoftNeighborsResponse)
def soft_neighbors_route(
    payload: SoftNeighborsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> SoftNeighborsResponse:
    ensure_workspace_readable(payload.workspace_id, settings)
    bad_kinds = [k for k in payload.edge_kinds if k not in ALLOWED_SOFT_KINDS]
    if bad_kinds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown soft edge kinds {bad_kinds!r}. Allowed: {sorted(ALLOWED_SOFT_KINDS)}"
            ),
        )
    neighbors = list_soft_neighbors(
        conn,
        workspace_id=payload.workspace_id,
        src_qualified_name=payload.src_qualified_name,
        edge_kinds=list(payload.edge_kinds) or None,
        limit=payload.limit,
    )
    return SoftNeighborsResponse(
        workspace_id=payload.workspace_id,
        src_qualified_name=payload.src_qualified_name,
        total=len(neighbors),
        neighbors=neighbors,
    )
