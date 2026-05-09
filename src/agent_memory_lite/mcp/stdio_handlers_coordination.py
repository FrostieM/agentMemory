"""1.7.0: stdio handlers for active-edit + soft_neighbors tools."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.mcp.stdio_guards import _with_workspace
from agent_memory_lite.mcp.stdio_http import _http_read, _http_write
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.mcp.tools_coordination import (
    memory_claim_edit,
    memory_list_active_edits,
    memory_release_edit,
    memory_soft_neighbors,
)


def _handle_claim_edit(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/claim_edit", payload, log_label="claim_edit")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_claim_edit(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        agent_id=str(payload["agent_id"]),
        qualified_name=payload.get("qualified_name"),
        file_path=payload.get("file_path"),
        ttl_minutes=int(payload.get("ttl_minutes", 30)),
        note=payload.get("note"),
    )


def _handle_release_edit(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/release_edit", payload, log_label="release_edit")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_release_edit(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        claim_id=str(payload["claim_id"]),
    )


def _handle_list_active_edits(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/list_active_edits", payload, log_label="list_active_edits")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_list_active_edits(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        limit=int(payload.get("limit", 100)),
    )


def _handle_soft_neighbors(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args, intent="read")
    delegated = _http_read("/memory/soft_neighbors", payload, log_label="soft_neighbors")
    if delegated is not None:
        return delegated
    workspace_id = str(payload["workspace_id"])
    return memory_soft_neighbors(
        conn=_runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        src_qualified_name=str(payload["src_qualified_name"]),
        edge_kinds=payload.get("edge_kinds"),
        limit=int(payload.get("limit", 20)),
    )
