"""1.8.0: stdio tool schemas for file digests."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

DIGEST_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_file_digest",
        description=(
            "1.8.0: get the narrative + structured digest for one file. "
            "Returns the cached summary built at ingest time — chunk "
            "count, symbol kinds, edge counts, recent version count, "
            "human-readable narrative."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "file_path": {"type": "string", "minLength": 1, "maxLength": 400},
            },
            "required": ["file_path"],
        },
    ),
    types.Tool(
        name="memory_list_file_digests",
        description=(
            "1.8.0: workspace overview — every file's digest, newest "
            "(most recently updated) first. Foundation for the v2.0 "
            "dashboard."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 500,
                },
            },
        },
    ),
]
