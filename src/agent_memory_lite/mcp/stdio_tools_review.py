"""Tool schemas for the candidate review + maintenance event surface."""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

REVIEW_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_list_candidates",
        description="List reviewable memory candidates created by extraction.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "query": {"type": "string"},
                "statuses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "since": {"type": "string"},
                "until": {"type": "string"},
            },
        },
    ),
    types.Tool(
        name="memory_promote_candidate",
        description="Promote a reviewed memory candidate into its explicit target table.",
        inputSchema={
            "type": "object",
            "properties": {"candidate_id": {"type": "string", "minLength": 1}},
            "required": ["candidate_id"],
        },
    ),
    types.Tool(
        name="memory_reject_candidate",
        description="Reject a memory candidate while preserving it for audit.",
        inputSchema={
            "type": "object",
            "properties": {"candidate_id": {"type": "string", "minLength": 1}},
            "required": ["candidate_id"],
        },
    ),
    types.Tool(
        name="memory_promote_candidate_to_behavior",
        description=(
            "Promote a CORRECTION-kind memory_candidate into a durable "
            "behavior_instruction. v1.10 correction-aware learning loop: "
            "when the operator corrected the agent in chat, the hook + "
            "extractor produced a candidate; this tool turns it into a "
            "behavior rule that surfaces in every future envelope."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "candidate_id": {"type": "string", "minLength": 1},
                "name": {"type": "string", "minLength": 1},
                "rule_text_override": {"type": "string"},
                "rationale": {"type": "string"},
                "kind": {"type": "string"},
                "scope": {"type": "string"},
                "priority": {"type": "string"},
                "conflict_policy": {"type": "string"},
                "applies_to": {"type": "array", "items": {"type": "string"}},
                "decided_by": {"type": "string"},
                "pinned": {"type": "boolean", "default": False},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["candidate_id", "name"],
        },
    ),
    types.Tool(
        name="memory_list_maintenance_events",
        description="List maintenance events that affect memory substrate trust.",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                "statuses": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
        },
    ),
    types.Tool(
        name="memory_resolve_maintenance_event",
        description="Mark a maintenance event resolved or ignored after review.",
        inputSchema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["resolved", "ignored"]},
            },
            "required": ["event_id"],
        },
    ),
]
