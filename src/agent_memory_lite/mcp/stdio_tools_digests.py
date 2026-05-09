"""1.8.0: stdio tool schemas for file digests."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

DIGEST_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_code_graph",
        description=(
            "2.1.2: node-link subgraph for D3 dashboard rendering. "
            "Pass ``center`` for BFS up to ``depth`` hops outward "
            "from one symbol; omit for top-K most-connected overview."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "center": {"type": "string", "maxLength": 400},
                "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 5},
                "max_nodes": {
                    "type": "integer",
                    "default": 200,
                    "minimum": 1,
                    "maximum": 1000,
                },
                "edge_kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "calls",
                            "imports",
                            "exports",
                            "extends",
                            "implements",
                            "references",
                            "instantiates",
                            "decorated_by",
                        ],
                    },
                },
            },
        },
    ),
    types.Tool(
        name="memory_code_overview",
        description=(
            "2.0: workspace dashboard payload — counts, recent files, "
            "breaking changes, active edits, most-called symbols. "
            "Single call returns everything the /ui/code dashboard "
            "renders."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "breaking_days": {
                    "type": "integer",
                    "default": 7,
                    "minimum": 1,
                    "maximum": 365,
                },
                "files_limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 200,
                },
            },
        },
    ),
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
