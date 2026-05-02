"""Aggregated MCP tool handler dispatch table."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_memory_lite.mcp.stdio_handlers_archive import _handle_archive
from agent_memory_lite.mcp.stdio_handlers_capabilities import (
    _handle_list_agent_capabilities,
    _handle_record_usage_feedback,
    _handle_upsert_agent_playbook,
    _handle_upsert_agent_role,
    _handle_upsert_agent_skill,
)
from agent_memory_lite.mcp.stdio_handlers_capability import (
    _handle_link_capability,
    _handle_list_behavior_instructions,
    _handle_list_capability_links,
    _handle_upsert_behavior_instruction,
)
from agent_memory_lite.mcp.stdio_handlers_decisions import (
    _handle_list_decisions,
    _handle_update_task_state,
    _handle_write_decision,
)
from agent_memory_lite.mcp.stdio_handlers_episodes import (
    _handle_get_context,
    _handle_get_object,
    _handle_ingest_episode,
    _handle_ingest_file,
    _handle_search,
)
from agent_memory_lite.mcp.stdio_handlers_p1 import (
    _handle_list_audit,
    _handle_pin,
    _handle_what_references,
)
from agent_memory_lite.mcp.stdio_handlers_research import (
    _handle_add_experiment_result,
    _handle_distill_insight,
    _handle_register_snapshot,
    _handle_update_insight,
    _handle_upsert_concept,
    _handle_write_experiment,
)
from agent_memory_lite.mcp.stdio_handlers_research_lists import (
    _handle_list_concepts,
    _handle_list_insights,
    _handle_list_research_agenda,
)
from agent_memory_lite.mcp.stdio_handlers_review import (
    _handle_list_candidates,
    _handle_list_maintenance_events,
    _handle_promote_candidate,
    _handle_reject_candidate,
    _handle_resolve_maintenance_event,
)
from agent_memory_lite.mcp.stdio_handlers_review_queue import (
    _handle_compact_trigger,
    _handle_review_queue,
)
from agent_memory_lite.mcp.stdio_handlers_state_snapshots import (
    _handle_snapshot_diff,
    _handle_snapshot_list,
    _handle_snapshot_save,
)
from agent_memory_lite.mcp.stdio_handlers_theories import (
    _handle_add_theory_evidence,
    _handle_list_theories,
    _handle_write_theory,
)

_Handler = Callable[[dict[str, Any]], dict[str, Any]]

_HANDLERS: dict[str, _Handler] = {
    "memory_get_context": _handle_get_context,
    "memory_get_object": _handle_get_object,
    "memory_search": _handle_search,
    "memory_ingest_episode": _handle_ingest_episode,
    "memory_write_decision": _handle_write_decision,
    "memory_list_decisions": _handle_list_decisions,
    "memory_update_task_state": _handle_update_task_state,
    "memory_ingest_file": _handle_ingest_file,
    "memory_list_candidates": _handle_list_candidates,
    "memory_promote_candidate": _handle_promote_candidate,
    "memory_reject_candidate": _handle_reject_candidate,
    "memory_list_maintenance_events": _handle_list_maintenance_events,
    "memory_resolve_maintenance_event": _handle_resolve_maintenance_event,
    "memory_link_capability": _handle_link_capability,
    "memory_list_capability_links": _handle_list_capability_links,
    "memory_upsert_behavior_instruction": _handle_upsert_behavior_instruction,
    "memory_list_behavior_instructions": _handle_list_behavior_instructions,
    "memory_write_theory": _handle_write_theory,
    "memory_add_theory_evidence": _handle_add_theory_evidence,
    "memory_list_theories": _handle_list_theories,
    "memory_register_snapshot": _handle_register_snapshot,
    "memory_write_experiment": _handle_write_experiment,
    "memory_add_experiment_result": _handle_add_experiment_result,
    "memory_upsert_concept": _handle_upsert_concept,
    "memory_distill_insight": _handle_distill_insight,
    "memory_update_insight": _handle_update_insight,
    "memory_list_research_agenda": _handle_list_research_agenda,
    "memory_list_concepts": _handle_list_concepts,
    "memory_list_insights": _handle_list_insights,
    "memory_upsert_agent_role": _handle_upsert_agent_role,
    "memory_upsert_agent_skill": _handle_upsert_agent_skill,
    "memory_upsert_agent_playbook": _handle_upsert_agent_playbook,
    "memory_list_agent_capabilities": _handle_list_agent_capabilities,
    "memory_record_usage_feedback": _handle_record_usage_feedback,
    "memory_archive": _handle_archive,
    "memory_pin": _handle_pin,
    "memory_what_references": _handle_what_references,
    "memory_list_audit": _handle_list_audit,
    "memory_snapshot_save": _handle_snapshot_save,
    "memory_snapshot_list": _handle_snapshot_list,
    "memory_snapshot_diff": _handle_snapshot_diff,
    "memory_review_queue": _handle_review_queue,
    "memory_compact_trigger": _handle_compact_trigger,
}
