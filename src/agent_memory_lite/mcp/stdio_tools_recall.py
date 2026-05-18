"""Phase 2 (skeleton) → Phase 7 (full): ``memory_recall`` MCP tool.

Phase 2 ships only the schema + a thin handler that returns spreading
activation over ``soft_edges``. Phase 7 will fold in
``capability_links``, ``causal_links`` (new), outcome filter, and
``as_of`` bi-temporal selection.

The Phase 2 surface is intentionally small so downstream agents can
already learn the call shape (kinds, depth, outcome_floor) while the
Hebbian log is still accruing.
"""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

RECALL_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_recall",
        description=(
            "Spreading-activation recall over the Hebbian soft-graph "
            "(``soft_edges``) seeded by a topic. Returns mixed-kind rows "
            "(decision / theory / behavior / skill / insight / chunk) "
            "ordered by accumulated activation. depth controls the BFS "
            "hop budget; outcome_floor (Phase 7) filters by Phase 1 "
            "outcome_score after spreading. Phase 2 returns 1-hop "
            "associates of the seeds; Phase 7 extends to causal_links "
            "and as_of bi-temporal selection."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "topic": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Natural-language topic; seeds via BM25 search.",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3,
                    "default": 2,
                    "description": "BFS hop budget. 1 = direct neighbours only.",
                },
                "outcome_floor": {
                    "type": "number",
                    "minimum": -1.0,
                    "maximum": 1.0,
                    "default": -1.0,
                    "description": "Drop results with outcome_score below this.",
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                    "description": "Restrict output to these kinds. Empty = all.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
                "as_of": {
                    "type": "string",
                    "description": (
                        "ISO 8601 timestamp -- recall as of this point in time. "
                        "Default = now. Phase 6 bi-temporal hook."
                    ),
                },
            },
            "required": ["topic"],
        },
    ),
]
