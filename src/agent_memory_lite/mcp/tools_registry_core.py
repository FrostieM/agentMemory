"""Core MCP tool definitions: episodes, review, decisions, theories."""

from __future__ import annotations

from agent_memory_lite.mcp.tools_decisions import (
    memory_list_decisions,
    memory_update_task_state,
    memory_write_decision,
)
from agent_memory_lite.mcp.tools_episodes import (
    memory_get_context,
    memory_ingest_episode,
    memory_ingest_file,
    memory_search,
)
from agent_memory_lite.mcp.tools_payloads import ToolDefinition
from agent_memory_lite.mcp.tools_review import (
    memory_list_candidates,
    memory_list_maintenance_events,
    memory_promote_candidate,
    memory_promote_candidate_to_behavior,
    memory_reject_candidate,
    memory_resolve_maintenance_event,
)
from agent_memory_lite.mcp.tools_symbols import memory_find_symbols
from agent_memory_lite.mcp.tools_theories import (
    memory_add_theory_evidence,
    memory_list_theories,
    memory_write_theory,
)

CORE_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="memory_get_context",
        description="Retrieve memory context for the agent before a task.",
        handler=memory_get_context,
    ),
    ToolDefinition(
        name="memory_ingest_episode",
        description="Persist an event into episodic memory with redaction.",
        handler=memory_ingest_episode,
    ),
    ToolDefinition(
        name="memory_search",
        description="Exact FTS lookup over chunks (BM25 ordered).",
        handler=memory_search,
    ),
    ToolDefinition(
        name="memory_find_symbols",
        description=(
            "1.4.0: exact symbol-level chunk lookup by qualified_name "
            "(e.g. 'Class.method' or 'Class::method' for C++) across "
            "the top-7 supported languages."
        ),
        handler=memory_find_symbols,
    ),
    ToolDefinition(
        name="memory_ingest_file",
        description="Index a single file into memory, idempotent by content hash.",
        handler=memory_ingest_file,
    ),
    ToolDefinition(
        name="memory_list_candidates",
        description="List reviewable memory candidates created by extraction.",
        handler=memory_list_candidates,
    ),
    ToolDefinition(
        name="memory_promote_candidate",
        description="Promote a reviewed memory candidate into its explicit target table.",
        handler=memory_promote_candidate,
    ),
    ToolDefinition(
        name="memory_promote_candidate_to_behavior",
        description=(
            "v1.10: promote a CORRECTION-kind candidate into a durable behavior_instruction."
        ),
        handler=memory_promote_candidate_to_behavior,
    ),
    ToolDefinition(
        name="memory_reject_candidate",
        description="Reject a memory candidate while preserving it as negative evidence.",
        handler=memory_reject_candidate,
    ),
    ToolDefinition(
        name="memory_list_maintenance_events",
        description="List open/resolved maintenance events that affect memory integrity.",
        handler=memory_list_maintenance_events,
    ),
    ToolDefinition(
        name="memory_resolve_maintenance_event",
        description="Mark a maintenance event resolved or ignored after review.",
        handler=memory_resolve_maintenance_event,
    ),
    ToolDefinition(
        name="memory_write_decision",
        description="Record an architectural decision; supports supersedes chains.",
        handler=memory_write_decision,
    ),
    ToolDefinition(
        name="memory_list_decisions",
        description="Search active or historical architectural decisions by topic.",
        handler=memory_list_decisions,
    ),
    ToolDefinition(
        name="memory_update_task_state",
        description="Upsert task state for (workspace_id, task_id).",
        handler=memory_update_task_state,
    ),
    ToolDefinition(
        name="memory_write_theory",
        description=(
            "Record a working research theory or hypothesis with claim, "
            "mechanism, predictions, and experiment plan."
        ),
        handler=memory_write_theory,
    ),
    ToolDefinition(
        name="memory_add_theory_evidence",
        description=(
            "Attach supporting, refuting, mixed, neutral, or experiment evidence to a theory."
        ),
        handler=memory_add_theory_evidence,
    ),
    ToolDefinition(
        name="memory_list_theories",
        description=(
            "List relevant theory/hypothesis memory items, optionally including recent evidence."
        ),
        handler=memory_list_theories,
    ),
)
