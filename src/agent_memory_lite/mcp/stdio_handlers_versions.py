"""1.6.0: stdio handlers for symbol_history + breaking_changes."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_read
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.tools_versions import (
    memory_breaking_changes,
    memory_symbol_history,
)


def _handle_symbol_history(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/symbol_history", payload, log_label="symbol_history")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_symbol_history(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        qualified_name=str(payload["qualified_name"]),
        limit=int(payload.get("limit", 20)),
    )


def _handle_breaking_changes(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/breaking_changes", payload, log_label="breaking_changes")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_breaking_changes(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        since_days=int(payload.get("since_days", 7)),
        limit=int(payload.get("limit", 50)),
        include_callers=bool(payload.get("include_callers", True)),
    )
