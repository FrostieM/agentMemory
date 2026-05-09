"""2.1.2: MCP tool — node-link subgraph (D3 dashboard data).

Mirrors GET /memory/code_graph so MCP-only deployments can ask for
a subgraph centered on a symbol or the workspace overview without
going through HTTP.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.api.routes.code_graph_bfs import bfs_from_center, overview


def memory_code_graph(
    *,
    conn: sqlite3.Connection,
    workspace_id: str = "default",
    center: str | None = None,
    depth: int = 2,
    max_nodes: int = 200,
    edge_kinds: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if center is not None:
        nodes, links, truncated = bfs_from_center(
            conn,
            workspace_id=workspace_id,
            center=center,
            depth=depth,
            max_nodes=max_nodes,
            edge_kinds=edge_kinds,
        )
    else:
        nodes, links, truncated = overview(
            conn,
            workspace_id=workspace_id,
            max_nodes=max_nodes,
            edge_kinds=edge_kinds,
        )
    return {
        "workspace_id": workspace_id,
        "center": center,
        "depth": depth,
        "nodes": [n.model_dump() for n in nodes],
        "links": [link.model_dump() for link in links],
        "truncated": truncated,
    }
