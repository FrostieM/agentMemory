"""Router-registration list for the FastAPI app factory.

Phase 3.3 of v2.2 consolidation. Split out of ``api/app.py`` so the
factory file stays under the ≤150-SLOC ceiling. The order of routers
matters when paths overlap (e.g. ``/ui/{asset_name}`` after
``/ui/code`` so the literal route wins), so the registration list is
explicit and stable rather than alphabetical.

Add a new router by importing it here and appending to ``ALL_ROUTERS``.
"""

from __future__ import annotations

from fastapi import FastAPI

from agent_memory_lite.api.routes import (
    active_edits,
    archive,
    audit_list,
    behavior,
    breaking_changes,
    candidates,
    capabilities,
    capability_links,
    code_graph,
    code_overview,
    cold_candidates,
    cold_decisions,
    compact,
    context,
    decision_candidates,
    decision_lineage,
    decisions,
    evals,
    explain_diff,
    feedback_summary,
    file_digests,
    find_symbols,
    get_object,
    graph_neighbors,
    health,
    hygiene,
    ingest_episode,
    ingest_file,
    insight_candidates,
    maintenance,
    memory_state_snapshots,
    memory_status,
    pin,
    promote_to_behavior,
    record_compound,
    recurring_findings,
    references,
    research,
    review_queue,
    search,
    sentinel_trends,
    soft_neighbors,
    symbol_history,
    task_state,
    telemetry,
    theories,
    ui,
    ui_pages,
    ui_vendor,
    usage,
    workspaces,
)
from agent_memory_lite.v3.api import routes as v3_routes

ALL_ROUTERS = (
    health,
    hygiene,
    behavior,
    candidates,
    capability_links,
    ingest_episode,
    ingest_file,
    maintenance,
    search,
    context,
    decisions,
    get_object,
    task_state,
    theories,
    research,
    capabilities,
    usage,
    feedback_summary,
    cold_candidates,
    cold_decisions,
    explain_diff,
    decision_lineage,
    decision_candidates,
    insight_candidates,
    sentinel_trends,
    recurring_findings,
    workspaces,
    ui,
    ui_pages,
    ui_vendor,
    compact,
    evals,
    archive,
    pin,
    references,
    audit_list,
    telemetry,
    memory_state_snapshots,
    memory_status,
    review_queue,
    promote_to_behavior,
    record_compound,
    # Code-memory family (v1.4 → v2.1.x).
    find_symbols,
    graph_neighbors,
    symbol_history,
    breaking_changes,
    active_edits,
    soft_neighbors,
    file_digests,
    code_overview,
    code_graph,
    # v3 surface (mounts at /v3/memory/*).
    v3_routes,
)


def register_all(app: FastAPI) -> None:
    """Wire every route module's router onto the FastAPI app."""
    for rt in ALL_ROUTERS:
        app.include_router(rt.router)
