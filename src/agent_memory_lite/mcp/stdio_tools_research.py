"""Tool schemas for the research-lab read surface (concepts + insights +
agenda) plus the snapshot/experiment writers (re-exported from
``stdio_tools_research_lab.py``)."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema
from agent_memory_lite.mcp.stdio_tools_research_lab import RESEARCH_LAB_TOOLS

_AGENDA_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_upsert_concept",
        description=(
            "Create or update a domain concept so research vocabulary is explicit and reusable."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "name": {"type": "string", "minLength": 1},
                "kind": {"type": "string", "default": "term"},
                "definition": {"type": "string", "minLength": 1},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "active": {"type": "boolean"},
            },
            "required": ["name", "definition"],
        },
    ),
    types.Tool(
        name="memory_distill_insight",
        description="Promote raw episode learnings into actionable insights or open questions.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "insight_type": {"type": "string"},
                "summary": {"type": "string", "minLength": 1},
                "proposed_action": {"type": "string"},
                "target_type": {"type": "string"},
                "target_id": {"type": "string"},
                "source_episode_ids": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "status": {"type": "string", "default": "new"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["insight_type", "summary"],
        },
    ),
    types.Tool(
        name="memory_update_insight",
        description="Update an existing research insight's target link or status.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "insight_id": {"type": "string", "minLength": 1},
                "target_type": {"type": "string", "minLength": 1},
                "target_id": {"type": "string", "minLength": 1},
                "status": {"type": "string"},
                "source_episode_id": {"type": "string"},
            },
            "required": ["insight_id"],
        },
    ),
    types.Tool(
        name="memory_list_research_agenda",
        description=(
            "List current snapshots, open experiments, insights, and concepts relevant to a query."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "since": {"type": "string"},
                "until": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="memory_list_concepts",
        description="List domain concepts in the project memory.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string"},
                "include_inactive": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    ),
    types.Tool(
        name="memory_list_insights",
        description="List distilled research insights and open questions.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    ),
]

RESEARCH_TOOLS: list[types.Tool] = [*RESEARCH_LAB_TOOLS, *_AGENDA_TOOLS]
