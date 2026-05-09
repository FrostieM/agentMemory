"""2.1.2: stdio handler for memory_code_graph."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_read
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.tools_code_graph import memory_code_graph


def _handle_code_graph(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/code_graph", payload, log_label="code_graph")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_code_graph(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        center=payload.get("center"),
        depth=int(payload.get("depth", 2)),
        max_nodes=int(payload.get("max_nodes", 200)),
        edge_kinds=payload.get("edge_kinds"),
    )
