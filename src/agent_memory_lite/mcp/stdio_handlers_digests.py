"""1.8.0: stdio handlers for file_digest + list_file_digests."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_read
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.tools_digests import (
    memory_file_digest,
    memory_list_file_digests,
)


def _handle_file_digest(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/file_digest", payload, log_label="file_digest")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_file_digest(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        file_path=str(payload["file_path"]),
    )


def _handle_list_file_digests(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/list_file_digests", payload, log_label="list_file_digests")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_list_file_digests(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        limit=int(payload.get("limit", 50)),
    )
