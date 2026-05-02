"""Tool schema for memory_archive."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

ARCHIVE_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_archive",
        description=(
            "Archive (or restore) a memory object so it stops appearing "
            "in get_context retrieval but stays searchable. Works across "
            "kinds: chunk, episode, file, decision, theory, insight, "
            "role, skill, playbook, behavior_instruction, candidate. "
            "Pass archive=false to restore."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "kind": {
                    "type": "string",
                    "enum": [
                        "chunk",
                        "episode",
                        "file",
                        "decision",
                        "theory",
                        "insight",
                        "role",
                        "skill",
                        "playbook",
                        "behavior_instruction",
                        "candidate",
                    ],
                },
                "id": {"type": "string", "minLength": 1},
                "archive": {"type": "boolean", "default": True},
            },
            "required": ["kind", "id"],
        },
    ),
]
