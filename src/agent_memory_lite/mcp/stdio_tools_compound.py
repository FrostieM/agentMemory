"""Tool schemas for Move 2 compound write tools.

`memory_record_with_evidence` bundles the three discipline-rule
follow-ups (ingest_episode → write_decision → link_capability) so the
agent calls one tool instead of remembering three. The capability link
is optional; pass the (type, name, relation) triplet to enable it,
omit all three to skip.
"""

from __future__ import annotations

from mcp import types

from agent_memory_lite.mcp.stdio_runtime import workspace_schema

COMPOUND_TOOLS: list[types.Tool] = [
    types.Tool(
        name="memory_record_with_evidence",
        description=(
            "Atomically ingest a supporting episode AND write a decision linked "
            "to that episode AND optionally link the decision to a capability "
            "(role / skill / playbook). One call replaces the 3-step ritual "
            "(ingest_episode → write_decision → link_capability). Returns all "
            "three created object ids. Move 2 of v2.2 consolidation: makes "
            "compliance the default rather than the requirement."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": workspace_schema(),
                # Evidence (required): becomes the supporting episode.
                "evidence_text": {"type": "string", "minLength": 1},
                "evidence_trust_level": {
                    "type": "string",
                    "default": "agent_observed",
                },
                "evidence_importance": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.6,
                },
                "evidence_source_type": {"type": "string", "default": "agent_action"},
                "session_id": {"type": "string"},
                "task_id": {"type": "string"},
                # Decision (required).
                "decision_title": {"type": "string", "minLength": 1},
                "decision_text": {"type": "string", "minLength": 1},
                "decision_rationale": {"type": "string"},
                "decision_importance": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.8,
                },
                "decision_confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.9,
                },
                "supersedes_decision_id": {"type": "string"},
                "references": {"type": "array", "items": {"type": "string"}},
                # Capability link (optional triplet — all-or-nothing).
                "capability_type": {
                    "type": "string",
                    "enum": ["role", "skill", "playbook"],
                },
                "capability_name": {"type": "string"},
                "capability_relation": {"type": "string"},
                "capability_rationale": {"type": "string"},
                "capability_strength": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.7,
                },
            },
            "required": ["evidence_text", "decision_title", "decision_text"],
        },
    ),
]
