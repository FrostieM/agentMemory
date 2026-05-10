"""Tool schemas for decisions + task state."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

DECISION_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_write_decision",
        description=(
            "Record an architectural decision. Pass supersedes_decision_id to "
            "close a prior decision atomically. Server auto-fills "
            "source_episode_id from the agent's most recent ingest_episode "
            "(Move 1, v2.2) unless allow_orphan=true. Response carries "
            "source_episode_id (resolved or null) AND capability_suggestions "
            "(top-3 ranked workspace roles/skills/playbooks, Move 3)."
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
                # 2.2 Move 1: opts out of auto-thread when the decision
                # deliberately has no episode (e.g. predates any
                # recording). Without this flag the server will look up
                # the agent's most recent ingest_episode in the same
                # workspace within MEMORY_AUTOTHREAD_WINDOW_MIN.
                "allow_orphan": {"type": "boolean", "default": False},
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
