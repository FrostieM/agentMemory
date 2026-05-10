"""GET /memory/code_graph — node-link subgraph for D3 dashboard.

2.1.2: collapses ``chunks`` (qualified_name, kind, language) and
``symbol_edges`` (calls / imports / extends / etc.) into a single
node-link payload the v2.1.2 ``/ui/graph`` page renders as a
force-directed graph.

Two modes:
* ``center``: BFS from one symbol up to ``depth`` hops outward.
* no ``center``: top-K most-connected symbols (overview mode).

Read-only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.code_graph_bfs import bfs_from_center, overview
from agent_memory_lite.api.routes.code_graph_models import CodeGraphResponse
from agent_memory_lite.models.symbol_edges import ALLOWED_EDGE_TYPES

router = APIRouter()

# Phase 3.4 (v2.2): the soft-edge family lives in a separate table
# (``soft_edges``) and uses different vocabulary than hard edges.
# Whitelisted explicitly so a typo in the query string surfaces 400.
ALLOWED_SOFT_EDGE_KINDS: frozenset[str] = frozenset({"similar_signature", "co_changed"})


@router.get("/memory/code_graph", response_model=CodeGraphResponse)
def code_graph_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
    center: str | None = Query(default=None, max_length=400),
    depth: int = Query(default=2, ge=1, le=5),
    max_nodes: int = Query(default=200, ge=1, le=1000),
    edge_kinds: Annotated[list[str] | None, Query()] = None,
    soft_edge_kinds: Annotated[list[str] | None, Query()] = None,
) -> CodeGraphResponse:
    ensure_workspace_readable(workspace_id, settings)
    if edge_kinds:
        bad = [k for k in edge_kinds if k not in ALLOWED_EDGE_TYPES]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown edge_kinds {bad!r}. Allowed: {sorted(ALLOWED_EDGE_TYPES)}",
            )
    if soft_edge_kinds:
        bad_soft = [k for k in soft_edge_kinds if k not in ALLOWED_SOFT_EDGE_KINDS]
        if bad_soft:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown soft_edge_kinds {bad_soft!r}. "
                    f"Allowed: {sorted(ALLOWED_SOFT_EDGE_KINDS)}"
                ),
            )
    if center is not None:
        nodes, links, truncated = bfs_from_center(
            conn,
            workspace_id=workspace_id,
            center=center,
            depth=depth,
            max_nodes=max_nodes,
            edge_kinds=edge_kinds,
            soft_edge_kinds=soft_edge_kinds,
        )
    else:
        nodes, links, truncated = overview(
            conn,
            workspace_id=workspace_id,
            max_nodes=max_nodes,
            edge_kinds=edge_kinds,
            soft_edge_kinds=soft_edge_kinds,
        )
    return CodeGraphResponse(
        workspace_id=workspace_id,
        center=center,
        depth=depth,
        nodes=nodes,
        links=links,
        truncated=truncated,
    )
