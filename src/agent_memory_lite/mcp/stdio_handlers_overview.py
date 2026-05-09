"""2.0: stdio handler for memory_code_overview."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_read
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.tools_overview import memory_code_overview


def _handle_code_overview(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/code_overview", payload, log_label="code_overview")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_code_overview(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        breaking_days=int(payload.get("breaking_days", 7)),
        files_limit=int(payload.get("files_limit", 20)),
    )
