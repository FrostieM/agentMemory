"""1.7.0: stdio tool schemas for the multi-agent coordination layer.

Split from ``stdio_tools_code.py`` so neither file exceeds the SLOC
ceiling. Holds the active-edit registry tools (``claim_edit``,
``release_edit``, ``list_active_edits``) and the soft-graph
``soft_neighbors`` lookup.
"""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

COORDINATION_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_claim_edit",
        description=(
            "1.7.0: claim a symbol or file for editing. Other agents "
            "see this via memory_list_active_edits. Returns "
            "claimed=false with blocked_by/blocked_until when another "
            "agent already has an active claim on the target."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "agent_id": {"type": "string", "minLength": 1, "maxLength": 64},
                "qualified_name": {"type": "string", "maxLength": 400},
                "file_path": {"type": "string", "maxLength": 400},
                "ttl_minutes": {
                    "type": "integer",
                    "default": 30,
                    "minimum": 1,
                    "maximum": 1440,
                },
                "note": {"type": "string", "maxLength": 500},
            },
            "required": ["agent_id"],
        },
    ),
    types.Tool(
        name="memory_release_edit",
        description="1.7.0: release a previously-claimed edit lock by claim_id.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "claim_id": {"type": "string", "minLength": 1, "maxLength": 64},
            },
            "required": ["claim_id"],
        },
    ),
    types.Tool(
        name="memory_list_active_edits",
        description=(
            "1.7.0: list every non-expired edit claim in the workspace "
            "so a starting agent can see who is touching what."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "limit": {
                    "type": "integer",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 500,
                },
            },
        },
    ),
    types.Tool(
        name="memory_soft_neighbors",
        description=(
            "1.7.0: heuristic graph neighbors for a symbol — symbols "
            "that co-change, co-reference, or have similar signatures. "
            "Use after a hard-graph lookup misses the connection you "
            "expected."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "src_qualified_name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 400,
                },
                "edge_kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "co_changed",
                            "co_referenced",
                            "similar_signature",
                        ],
                    },
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            "required": ["src_qualified_name"],
        },
    ),
]
