"""Tool schemas for the P1 surface: memory_pin / memory_what_references
/ memory_list_audit."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

P1_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_pin",
        description=(
            "Pin (or un-pin) a high-priority memory object so it is "
            "always included in the active context envelope regardless "
            "of query relevance or token budget. Supported kinds: "
            "'decision', 'behavior_instruction', 'core_memory'. "
            "Roles/skills/playbooks intentionally stay un-pinned — "
            "they already ride the capability ranker."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {
                    "type": "string",
                    "enum": ["decision", "behavior_instruction", "core_memory"],
                },
                "id": {"type": "string", "minLength": 1},
                "pinned": {"type": "boolean", "default": True},
            },
            "required": ["kind", "id"],
        },
    ),
    types.Tool(
        name="memory_what_references",
        description=(
            "Reverse lookup: given a target id (decision, theory, "
            "episode, role, …), return every memory row that mentions "
            "it across decisions, theories, insights, experiments, "
            "snapshots, chunks, episodes, behavior_instructions, and "
            "capability_links."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "target_id": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["target_id"],
        },
    ),
    types.Tool(
        name="memory_list_audit",
        description=(
            "Read the audit log (who/when/before/after) for a target "
            "or for the whole workspace. Filters: target_type, "
            "target_id, action, since, until."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "target_type": {"type": "string"},
                "target_id": {"type": "string"},
                "action": {"type": "string"},
                "since": {"type": "string"},
                "until": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
        },
    ),
]
