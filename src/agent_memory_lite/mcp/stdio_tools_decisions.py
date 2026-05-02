"""Tool schemas for decisions + task state."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

DECISION_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_write_decision",
        description=(
            "Record an architectural decision. Pass supersedes_decision_id to "
            "close a prior decision atomically."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "title": {"type": "string", "minLength": 1},
                "decision_text": {"type": "string", "minLength": 1},
                "rationale": {"type": "string"},
                "supersedes_decision_id": {"type": "string"},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.9},
                "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.8},
            },
            "required": ["title", "decision_text"],
        },
    ),
    types.Tool(
        name="memory_list_decisions",
        description=(
            "Search active or historical architectural decisions by topic. Use this "
            "when you need a global view such as decisions about live execution."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string"},
                "include_superseded": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
            },
        },
    ),
    types.Tool(
        name="memory_update_task_state",
        description="Upsert task state keyed by (workspace_id, task_id).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "task_id": {"type": "string", "minLength": 1},
                "goal": {"type": "string", "minLength": 1},
                "status": {"type": "string", "minLength": 1},
                "current_plan": {"type": "array", "items": {"type": "string"}},
                "completed_steps": {"type": "array", "items": {"type": "string"}},
                "next_action": {"type": "string"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "files_in_scope": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
            },
            "required": ["task_id", "goal", "status"],
        },
    ),
]
