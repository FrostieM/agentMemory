"""Tool schemas for agent capabilities (roles/skills/playbooks) +
record_usage_feedback."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

CAPABILITY_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_upsert_agent_role",
        description=(
            "Create or update a first-class agent role with responsibilities and boundaries."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "purpose": {"type": "string", "minLength": 1},
                "responsibilities": {"type": "array", "items": {"type": "string"}},
                "boundaries": {"type": "array", "items": {"type": "string"}},
                "handoff_triggers": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "purpose"],
        },
    ),
    types.Tool(
        name="memory_upsert_agent_skill",
        description=(
            "Create or update a reusable agent skill with inputs, outputs, and related roles."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "summary": {"type": "string", "minLength": 1},
                "when_to_use": {"type": "array", "items": {"type": "string"}},
                "inputs": {"type": "array", "items": {"type": "string"}},
                "outputs": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "string"}},
                "related_roles": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "summary"],
        },
    ),
    types.Tool(
        name="memory_upsert_agent_playbook",
        description=(
            "Create or update a repeatable agent playbook with triggers, steps, and success criteria."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "goal": {"type": "string", "minLength": 1},
                "triggers": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "array", "items": {"type": "string"}},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "required_skills": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "goal"],
        },
    ),
    types.Tool(
        name="memory_list_agent_capabilities",
        description="List relevant roles, skills, and playbooks for a query.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string"},
                "include_inactive": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 6},
            },
        },
    ),
    types.Tool(
        name="memory_record_usage_feedback",
        description=(
            "Record whether a retrieved memory item was helpful or noisy so "
            "future ranking can improve."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "source_type": {
                    "type": "string",
                    "enum": ["chunk", "decision", "theory", "insight", "capability"],
                    "default": "chunk",
                },
                "source_id": {"type": "string", "minLength": 1},
                "query": {"type": "string"},
                "usefulness": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                "task_id": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["source_id", "usefulness"],
        },
    ),
]
