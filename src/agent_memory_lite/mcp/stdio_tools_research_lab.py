"""Tool schemas for the research-lab write surface (snapshots/experiments)."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

RESEARCH_LAB_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_register_snapshot",
        description=(
            "Register or update a research data snapshot with paths, build "
            "metadata, and table counts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "snapshot_key": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "source": {"type": "string", "default": "manual"},
                "db_path": {"type": "string"},
                "duckdb_path": {"type": "string"},
                "parquet_dir": {"type": "string"},
                "window_start": {"type": "string"},
                "window_end": {"type": "string"},
                "build_sha": {"type": "string"},
                "build_branch": {"type": "string"},
                "build_time": {"type": "string"},
                "remote_host": {"type": "string"},
                "table_counts": {"type": "object"},
                "total_rows": {"type": "integer", "minimum": 0},
                "metadata": {"type": "object"},
                "source_episode_id": {"type": "string"},
            },
            "required": ["snapshot_key", "title"],
        },
    ),
    types.Tool(
        name="memory_write_experiment",
        description=(
            "Create a planned/running research experiment linked to a theory and/or data snapshot."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "theory_id": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "title": {"type": "string", "minLength": 1},
                "hypothesis": {"type": "string", "minLength": 1},
                "cohort_definition": {"type": "string"},
                "success_criteria": {"type": "object"},
                "command": {"type": "string"},
                "status": {"type": "string", "default": "planned"},
                "priority": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "owner": {"type": "string"},
                "due_at": {"type": "string"},
                "source_episode_id": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["title", "hypothesis"],
        },
    ),
    types.Tool(
        name="memory_add_experiment_result",
        description=(
            "Record an experiment result; linked theory confidence/status is updated automatically."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "experiment_id": {"type": "string", "minLength": 1},
                "theory_id": {"type": "string"},
                "kind": {"type": "string"},
                "summary": {"type": "string", "minLength": 1},
                "metrics": {"type": "object"},
                "artifact_path": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "observed_at": {"type": "string"},
                "source_episode_id": {"type": "string"},
            },
            "required": ["experiment_id", "kind", "summary"],
        },
    ),
]
