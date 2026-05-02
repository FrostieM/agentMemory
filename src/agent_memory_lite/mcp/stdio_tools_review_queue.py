"""MCP tool schemas for the review queue + compact-trigger surface."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

REVIEW_QUEUE_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_review_queue",
        description=(
            "Operator-focused queue of memory rows that need an "
            "explicit decision right now (promote/reject candidates, "
            "resolve maintenance events). Each item carries the "
            "suggested action so the agent / UI knows which endpoint "
            "to call. Distinct from /memory/hygiene_report which is a "
            "broader content-quality scan."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "limit_per_kind": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
            },
        },
    ),
    types.Tool(
        name="memory_compact_trigger",
        description=(
            "Probe whether the workspace's chunk count + stale ratio "
            "is past the configured compaction threshold and emit a "
            "compaction_due maintenance event when overdue. Never "
            "runs compaction itself. Off by default; enable with "
            "MEMORY_COMPACT_TRIGGER_THRESHOLD_CHUNKS > 0."
        ),
        inputSchema={
            "type": "object",
            "properties": {"workspace_id": workspace_schema()},
        },
    ),
]
