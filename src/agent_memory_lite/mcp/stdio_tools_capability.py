"""Tool schemas for capability-link + behavior-instruction surface."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

CAPABILITY_LINK_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_link_capability",
        description=(
            "Link a role, skill, or playbook to a research object so it can "
            "influence hypothesis retrieval and context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "target_type": {"type": "string"},
                "target_id": {"type": "string", "minLength": 1},
                "capability_type": {"type": "string"},
                "capability_id": {"type": "string"},
                "capability_name": {"type": "string"},
                "relation": {"type": "string", "default": "method"},
                "rationale": {"type": "string"},
                "strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "source_episode_id": {"type": "string"},
            },
            "required": ["target_type", "target_id", "capability_type"],
        },
    ),
    types.Tool(
        name="memory_list_capability_links",
        description="List links from roles, skills, and playbooks to research memory objects.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "target_type": {"type": "string"},
                "target_id": {"type": "string"},
                "capability_type": {"type": "string"},
                "capability_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
    ),
    types.Tool(
        name="memory_upsert_behavior_instruction",
        description=(
            "Create or update a persistent behavior instruction with explicit "
            "kind, scope, priority, and conflict policy."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "rule": {"type": "string", "minLength": 1},
                "kind": {
                    "type": "string",
                    "enum": [
                        "communication_style",
                        "operating_rule",
                        "project_convention",
                        "workflow_preference",
                        "role_guidance",
                    ],
                    "default": "operating_rule",
                },
                "scope": {
                    "type": "string",
                    "enum": ["global", "workspace", "project", "task", "role"],
                    "default": "workspace",
                },
                "priority": {
                    "type": "string",
                    "enum": [
                        "system_bound",
                        "user_preference",
                        "project_convention",
                        "suggestion",
                    ],
                    "default": "user_preference",
                },
                "rationale": {"type": "string"},
                "applies_to": {"type": "array", "items": {"type": "string"}},
                "conflict_policy": {
                    "type": "string",
                    "enum": [
                        "system_wins",
                        "current_user_wins",
                        "higher_priority_wins",
                        "most_specific_wins",
                        "latest_wins",
                    ],
                    "default": "current_user_wins",
                },
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "rule"],
        },
    ),
    types.Tool(
        name="memory_list_behavior_instructions",
        description="List persistent behavior instructions for agent communication and operating behavior.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string"},
                "kinds": {"type": "array", "items": {"type": "string"}},
                "include_inactive": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "since": {"type": "string"},
                "until": {"type": "string"},
            },
        },
    ),
]
