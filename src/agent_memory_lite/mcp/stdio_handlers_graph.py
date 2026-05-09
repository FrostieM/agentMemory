"""1.5.0: stdio handler for ``memory_graph_neighbors``.

Tries the warm HTTP service first (so a single-workspace project mode
benefits from middleware / audit), falls back to local SQLite via
``tools_graph.memory_graph_neighbors``.
"""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_read
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.tools_graph import memory_graph_neighbors


def _handle_graph_neighbors(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/graph_neighbors", payload, log_label="graph_neighbors")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_graph_neighbors(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        qualified_name=payload.get("qualified_name"),
        chunk_id=payload.get("chunk_id"),
        edge_types=payload.get("edge_types"),
        direction=str(payload.get("direction", "both")),
        limit=int(payload.get("limit", 100)),
    )
