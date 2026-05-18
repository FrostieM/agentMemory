"""Aggregated MCP tool handler dispatch table.

19 legacy v2 dispatch entries were removed on 2026-05-18 -- the
compat shim (v2_compat.py, default ON) routes those names to the
canonical backend so the native handlers became unreachable dead
code. The handler functions themselves remain in their per-domain
sibling files for now (separate cleanup commit) but are no longer
imported from this dispatcher.

Removed entries (legacy v2 name -> served by):
  memory_get_object              shim -> memory_get
  memory_search                  canonical (V3) -> memory_search
  memory_ingest_episode          shim -> memory_write(kind=episode)
  memory_write_decision          shim -> memory_write(kind=decision)
  memory_record_with_evidence    shim -> memory_write x2
  memory_list_decisions          shim -> memory_list/memory_search
  memory_upsert_behavior_instruction  shim -> memory_write(kind=behavior)
  memory_list_behavior_instructions   shim -> memory_list
  memory_write_theory            shim -> memory_write(kind=theory)
  memory_list_theories           shim -> memory_list
  memory_upsert_concept          shim -> memory_write(kind=concept)
  memory_list_concepts           shim -> memory_list
  memory_list_insights           shim -> memory_list
  memory_upsert_agent_role/skill/playbook  shim -> memory_write(kind=skill)
  memory_list_agent_capabilities shim -> memory_list
  memory_archive                 canonical (V3) -> memory_archive
  memory_pin                     canonical (V3) -> memory_pin
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_memory_lite.mcp.stdio_handlers_capabilities import (
    _handle_record_usage_feedback,
)
from agent_memory_lite.mcp.stdio_handlers_capability import (
    _handle_link_capability,
    _handle_list_capability_links,
)
from agent_memory_lite.mcp.stdio_handlers_code_graph import _handle_code_graph
from agent_memory_lite.mcp.stdio_handlers_coordination import (
    _handle_claim_edit,
    _handle_list_active_edits,
    _handle_release_edit,
    _handle_soft_neighbors,
)
from agent_memory_lite.mcp.stdio_handlers_decisions import (
    _handle_update_task_state,
)
from agent_memory_lite.mcp.stdio_handlers_digests import (
    _handle_file_digest,
    _handle_list_file_digests,
)
from agent_memory_lite.mcp.stdio_handlers_episodes import (
    _handle_get_context,
    _handle_ingest_file,
)
from agent_memory_lite.mcp.stdio_handlers_graph import _handle_graph_neighbors
from agent_memory_lite.mcp.stdio_handlers_memory import MEMORY_HANDLERS
from agent_memory_lite.mcp.stdio_handlers_overview import _handle_code_overview
from agent_memory_lite.mcp.stdio_handlers_p1 import (
    _handle_list_audit,
    _handle_what_references,
)
from agent_memory_lite.mcp.stdio_handlers_research import (
    _handle_add_experiment_result,
    _handle_distill_insight,
    _handle_register_snapshot,
    _handle_update_insight,
    _handle_write_experiment,
)
from agent_memory_lite.mcp.stdio_handlers_research_lists import (
    _handle_list_research_agenda,
)
from agent_memory_lite.mcp.stdio_handlers_review import (
    _handle_list_candidates,
    _handle_list_maintenance_events,
    _handle_promote_candidate,
    _handle_promote_candidate_to_behavior,
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
from agent_memory_lite.mcp.stdio_handlers_symbols import _handle_find_symbols
from agent_memory_lite.mcp.stdio_handlers_theories import (
    _handle_add_theory_evidence,
)
from agent_memory_lite.mcp.stdio_handlers_versions import (
    _handle_breaking_changes,
    _handle_symbol_history,
)

_Handler = Callable[[dict[str, Any]], dict[str, Any]]

_HANDLERS: dict[str, _Handler] = {
    # v2 names without a canonical equivalent (kept as native handlers).
    "memory_get_context": _handle_get_context,
    "memory_find_symbols": _handle_find_symbols,
    "memory_graph_neighbors": _handle_graph_neighbors,
    "memory_symbol_history": _handle_symbol_history,
    "memory_breaking_changes": _handle_breaking_changes,
    "memory_claim_edit": _handle_claim_edit,
    "memory_release_edit": _handle_release_edit,
    "memory_list_active_edits": _handle_list_active_edits,
    "memory_soft_neighbors": _handle_soft_neighbors,
    "memory_file_digest": _handle_file_digest,
    "memory_list_file_digests": _handle_list_file_digests,
    "memory_code_overview": _handle_code_overview,
    "memory_code_graph": _handle_code_graph,
    "memory_update_task_state": _handle_update_task_state,
    "memory_ingest_file": _handle_ingest_file,
    "memory_list_candidates": _handle_list_candidates,
    "memory_promote_candidate": _handle_promote_candidate,
    "memory_promote_candidate_to_behavior": _handle_promote_candidate_to_behavior,
    "memory_reject_candidate": _handle_reject_candidate,
    "memory_list_maintenance_events": _handle_list_maintenance_events,
    "memory_resolve_maintenance_event": _handle_resolve_maintenance_event,
    "memory_link_capability": _handle_link_capability,
    "memory_list_capability_links": _handle_list_capability_links,
    "memory_add_theory_evidence": _handle_add_theory_evidence,
    "memory_register_snapshot": _handle_register_snapshot,
    "memory_write_experiment": _handle_write_experiment,
    "memory_add_experiment_result": _handle_add_experiment_result,
    "memory_distill_insight": _handle_distill_insight,
    "memory_update_insight": _handle_update_insight,
    "memory_list_research_agenda": _handle_list_research_agenda,
    "memory_record_usage_feedback": _handle_record_usage_feedback,
    "memory_what_references": _handle_what_references,
    "memory_list_audit": _handle_list_audit,
    "memory_snapshot_save": _handle_snapshot_save,
    "memory_snapshot_list": _handle_snapshot_list,
    "memory_snapshot_diff": _handle_snapshot_diff,
    "memory_review_queue": _handle_review_queue,
    "memory_compact_trigger": _handle_compact_trigger,
    # Canonical surface -- agent-facing minimal tool set, ``{ok, data, error}`` envelope.
    # Legacy v2 names like ``memory_write_decision`` / ``memory_ingest_episode`` are routed
    # here via ``v2_compat.compat_dispatch`` (default ON) and never hit a native handler.
    **MEMORY_HANDLERS,
}
