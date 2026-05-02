"""MCP handlers for memory state snapshots."""

from __future__ import annotations

from typing import Any

from agent_memory_lite.ingestion.memory_state_snapshot_diff import diff_state_snapshots
from agent_memory_lite.ingestion.memory_state_snapshot_writer import capture_state_snapshot
from agent_memory_lite.mcp.stdio_guards import _with_workspace, _workspace_from_args
from agent_memory_lite.mcp.stdio_http import _http_write
from agent_memory_lite.mcp.stdio_runtime import _runtime
from agent_memory_lite.models.memory_state_snapshots import MemoryStateSnapshot
from agent_memory_lite.repositories.memory_state_snapshots_repo import (
    get_state_snapshot,
    list_state_snapshots,
)


def _payload(snapshot: MemoryStateSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.id,
        "workspace_id": snapshot.workspace_id,
        "name": snapshot.name,
        "taken_at": snapshot.taken_at,
        "counts": snapshot.counts,
        "metadata": snapshot.metadata,
        "created_at": snapshot.created_at,
    }


def _handle_snapshot_save(args: dict[str, Any]) -> dict[str, Any]:
    payload = _with_workspace(args)
    delegated = _http_write("/memory/snapshot_save", payload, log_label="snapshot_save")
    if delegated is not None:
        return delegated
    snapshot = capture_state_snapshot(
        _runtime.db(),
        workspace_id=str(payload["workspace_id"]),
        name=payload.get("name"),
        metadata=payload.get("metadata") or {},
    )
    return _payload(snapshot)


def _handle_snapshot_list(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    snapshots = list_state_snapshots(
        _runtime.db_for(workspace_id),
        workspace_id=workspace_id,
        limit=int(args.get("limit", 20)),
    )
    return {"snapshots": [_payload(item) for item in snapshots]}


def _handle_snapshot_diff(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _workspace_from_args(args, intent="read")
    conn = _runtime.db_for(workspace_id)
    before = get_state_snapshot(conn, str(args["before_id"]))
    after = get_state_snapshot(conn, str(args["after_id"]))
    if before is None or after is None:
        missing = [
            label for label, value in (("before_id", before), ("after_id", after)) if value is None
        ]
        raise ValueError(f"snapshot not found: {', '.join(missing)}")
    diff = diff_state_snapshots(before, after)
    return {
        "before_snapshot_id": diff.before_snapshot_id,
        "after_snapshot_id": diff.after_snapshot_id,
        "before_taken_at": diff.before_taken_at,
        "after_taken_at": diff.after_taken_at,
        "counts_delta": diff.counts_delta,
        "added": diff.added,
        "removed": diff.removed,
        "changed": diff.changed,
    }
