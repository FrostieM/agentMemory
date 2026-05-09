"""1.5.0: MCP tool — hard-graph upstream / downstream lookup.

Mirrors POST /memory/graph_neighbors so MCP-only deployments can ask
"what calls Foo.bar?" or "what does Selector.admit depend on?"
without paying for FTS / vector retrieval.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.repositories.symbol_edges_repo import (
    list_edges_from,
    list_edges_to,
)


def memory_graph_neighbors(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    qualified_name: str | None = None,
    chunk_id: str | None = None,
    edge_types: list[str] | None = None,
    direction: str = "both",
    limit: int = 100,
    **_kwargs: Any,
) -> dict[str, Any]:
    upstream: list[dict[str, Any]] = []
    downstream: list[dict[str, Any]] = []
    if direction in ("downstream", "both") and chunk_id is not None:
        downstream = [
            _edge_to_dict(e)
            for e in list_edges_from(
                conn,
                workspace_id=workspace_id,
                src_chunk_id=chunk_id,
                edge_types=edge_types or None,
                limit=limit,
            )
        ]
    if direction in ("upstream", "both") and qualified_name is not None:
        upstream = [
            _edge_to_dict(e)
            for e in list_edges_to(
                conn,
                workspace_id=workspace_id,
                dst_qualified_name=qualified_name,
                edge_types=edge_types or None,
                limit=limit,
            )
        ]
    return {
        "workspace_id": workspace_id,
        "upstream": upstream,
        "downstream": downstream,
    }


def _edge_to_dict(edge: Any) -> dict[str, Any]:
    return {
        "edge_id": edge.id,
        "edge_type": edge.edge_type,
        "src_chunk_id": edge.src_chunk_id,
        "src_qualified_name": edge.src_qualified_name,
        "dst_qualified_name": edge.dst_qualified_name,
        "dst_chunk_id": edge.dst_chunk_id,
        "src_language": edge.src_language,
    }
