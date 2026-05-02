"""MCP tool schemas for memory state snapshots.

Memory state snapshots are point-in-time digests of the workspace
itself (not external research datasets). They power "what changed
in my memory between A and B" diffs without scrolling the audit
log.
"""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

STATE_SNAPSHOT_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_snapshot_save",
        description=(
            "Capture a point-in-time digest of the workspace memory "
            "(counts and short hashes per kind). Useful for "
            "before/after comparisons across long tasks or deploys."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "name": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    ),
    types.Tool(
        name="memory_snapshot_list",
        description=(
            "List previously captured memory state snapshots, newest "
            "first. Each entry includes counts but not the per-row "
            "digest map."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
        },
    ),
    types.Tool(
        name="memory_snapshot_diff",
        description=(
            "Diff two memory state snapshots: counts deltas, added "
            "ids, removed ids, and content-changed ids (same id, "
            "different short hash)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "before_id": {"type": "string", "minLength": 1},
                "after_id": {"type": "string", "minLength": 1},
            },
            "required": ["before_id", "after_id"],
        },
    ),
]
