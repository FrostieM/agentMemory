"""Tool schemas for v3.1 active-memory vectors.

* ``memory_propose_experiments`` — Vector 1. Scan uncertain insights
  and return proto-theory proposals (read-only preview by default;
  set ``persist=true`` to land them as ``memory_candidate`` rows).
* ``memory_predictive_warnings`` — Vector 5. Read-only preview of
  active decisions that token-overlap known low-outcome failures.

The MCP surface mirrors the HTTP routes 1:1 so an agent without HTTP
access (in-process MCP) still gets the same observability the
operator's curl would.
"""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

V3_1_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_propose_experiments",
        description=(
            "v3.1 Vector 1: scan uncertain insights and return proto-theory "
            "proposals with pre-filled validation criteria. Heuristic only "
            "(no LLM yet). Set ``persist=true`` to also write each proposal "
            "as a ``memory_candidate(kind=theory_proposal)`` row "
            "(idempotent via deterministic candidate id)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "persist": {
                    "type": "boolean",
                    "default": False,
                    "description": "When true, write candidate rows in addition to returning them.",
                },
            },
        },
    ),
    types.Tool(
        name="memory_predictive_warnings",
        description=(
            "v3.1 Vector 5: surface active decisions that token-overlap a "
            "known low-outcome decision (Jaccard heuristic). Pure read; "
            "warnings carry similarity score + reason text so the agent "
            "can decide whether to abort or proceed with a pattern "
            "previously associated with failure."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    ),
]
