"""Handlers for the P1 tools: memory_pin / memory_what_references /
memory_list_audit."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion.pin_service import pin_memory_object
from agent_memory_lite.mcp.stdio_guards import _with_workspace, _workspace_from_args
from agent_memory_lite.mcp.stdio_http import _http_write
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.repositories.audit_list_repo import list_audit_entries
from agent_memory_lite.repositories.references_repo import find_references


def _handle_pin(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/pin", payload, log_label="pin")
    if delegated is not None:
        return delegated
    pinned = bool(payload.get("pinned", True))
    return pin_memory_object(
        _runtime.db_for(str(payload["workspace_id"])),
        workspace_id=str(payload["workspace_id"]),
        kind=str(payload["kind"]),
        object_id=str(payload["id"]),
        pinned=pinned,
    )


def _handle_what_references(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    target_id = str(args.get("target_id", "")).strip()
    limit = int(args.get("limit", 50))
    hits = find_references(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        target_id=target_id,
        limit=limit,
    )
    return {
        "target_id": target_id,
        "hits": [
            {
                "table": h.table,
                "kind": h.kind,
                "id": h.id,
                "label": h.label,
                "column": h.column,
            }
            for h in hits
        ],
    }


def _handle_list_audit(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    rows = list_audit_entries(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        target_type=args.get("target_type"),
        target_id=args.get("target_id"),
        action=args.get("action"),
        since=args.get("since"),
        until=args.get("until"),
        limit=int(args.get("limit", 50)),
    )
    return {
        "entries": [
            {
                "id": row.id,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "source_episode_id": row.source_episode_id,
                "before": row.before,
                "after": row.after,
                "created_at": row.created_at,
            }
            for row in rows
        ],
    }
