"""POST /memory/graph_neighbors — hard-graph upstream / downstream lookup.

1.5.0: given a symbol identifier (qualified_name OR chunk_id), return
edges in both directions:

* downstream: outbound edges (what does this symbol use? — calls /
  imports / extends / decorated_by / instantiates / references)
* upstream:   inbound edges (who uses this symbol? — same edge types
  in reverse)

This is the "find every place that depends on Foo.bar" capability the
README headlines as the v1.5.0 deliverable. Pre-1.5.0 it required
substring search across every chunk body, which mixed false positives.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.models.symbol_edges import ALLOWED_EDGE_TYPES, SymbolEdge
from agent_memory_lite.repositories.symbol_edges_repo import (
    list_edges_from,
    list_edges_to,
)

router = APIRouter()


class GraphNeighborsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    qualified_name: str | None = Field(default=None, max_length=400)
    chunk_id: str | None = Field(default=None, max_length=64)
    edge_types: list[str] = Field(default_factory=list)
    direction: str = Field(
        default="both",
        description="'upstream' | 'downstream' | 'both'",
    )
    limit: int = Field(default=100, ge=1, le=500)


class NeighborEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edge_id: str
    edge_type: str
    src_chunk_id: str
    src_qualified_name: str
    dst_qualified_name: str
    dst_chunk_id: str | None
    src_language: str | None


class GraphNeighborsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    upstream: list[NeighborEdge]
    downstream: list[NeighborEdge]


@router.post("/memory/graph_neighbors", response_model=GraphNeighborsResponse)
def graph_neighbors_route(
    payload: GraphNeighborsRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> GraphNeighborsResponse:
    ensure_workspace_readable(payload.workspace_id, settings)

    if payload.qualified_name is None and payload.chunk_id is None:
        raise HTTPException(
            status_code=400,
            detail="graph_neighbors requires either qualified_name or chunk_id",
        )
    if payload.direction not in ("upstream", "downstream", "both"):
        raise HTTPException(
            status_code=400,
            detail=f"unknown direction {payload.direction!r}; expected upstream / downstream / both",
        )
    bad_kinds = [k for k in payload.edge_types if k not in ALLOWED_EDGE_TYPES]
    if bad_kinds:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown edge_types {bad_kinds!r}. Allowed: {sorted(ALLOWED_EDGE_TYPES)}",
        )

    edge_types = list(payload.edge_types) or None

    downstream: list[NeighborEdge] = []
    upstream: list[NeighborEdge] = []

    if payload.direction in ("downstream", "both") and payload.chunk_id is not None:
        rows = list_edges_from(
            conn,
            workspace_id=payload.workspace_id,
            src_chunk_id=payload.chunk_id,
            edge_types=edge_types,
            limit=payload.limit,
        )
        downstream = [_to_neighbor(r) for r in rows]

    if payload.direction in ("upstream", "both") and payload.qualified_name is not None:
        rows = list_edges_to(
            conn,
            workspace_id=payload.workspace_id,
            dst_qualified_name=payload.qualified_name,
            edge_types=edge_types,
            limit=payload.limit,
        )
        upstream = [_to_neighbor(r) for r in rows]

    return GraphNeighborsResponse(
        workspace_id=payload.workspace_id,
        upstream=upstream,
        downstream=downstream,
    )


def _to_neighbor(edge: SymbolEdge) -> NeighborEdge:
    return NeighborEdge(
        edge_id=edge.id,
        edge_type=edge.edge_type,
        src_chunk_id=edge.src_chunk_id,
        src_qualified_name=edge.src_qualified_name,
        dst_qualified_name=edge.dst_qualified_name,
        dst_chunk_id=edge.dst_chunk_id,
        src_language=edge.src_language,
    )
