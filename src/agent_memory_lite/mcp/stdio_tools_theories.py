"""Tool schemas for theories + theory evidence."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

THEORY_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_write_theory",
        description=(
            "Record a working research theory with claim, mechanism, "
            "predictions, and experiment plan."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "title": {"type": "string", "minLength": 1},
                "claim": {"type": "string", "minLength": 1},
                "domain": {"type": "string", "default": "general"},
                "mechanism": {"type": "string"},
                "predictions": {"type": "array", "items": {"type": "string"}},
                "validation_criteria": {"type": "array", "items": {"type": "string"}},
                "experiment_plan": {"type": "string"},
                "dependent_decision_ids": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "status": {"type": "string", "default": "proposed"},
                "supersedes_theory_id": {"type": "string"},
                "source_episode_id": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["title", "claim"],
        },
    ),
    types.Tool(
        name="memory_add_theory_evidence",
        description=(
            "Attach supporting, refuting, mixed, neutral, or experiment evidence to a theory."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "theory_id": {"type": "string", "minLength": 1},
                "kind": {"type": "string"},
                "summary": {"type": "string", "minLength": 1},
                "source_episode_id": {"type": "string"},
                "artifact_path": {"type": "string"},
                "metrics": {"type": "object"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "observed_at": {"type": "string"},
            },
            "required": ["theory_id", "kind", "summary"],
        },
    ),
    types.Tool(
        name="memory_list_theories",
        description="List relevant theory memory items, optionally including recent evidence.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "include_archived": {"type": "boolean", "default": False},
                "include_evidence": {"type": "boolean", "default": False},
                "evidence_limit": {"type": "integer", "minimum": 0, "maximum": 20, "default": 3},
            },
        },
    ),
]
