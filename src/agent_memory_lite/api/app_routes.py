"""Router-registration list for the FastAPI app factory.

Phase 3.3 of v2.2 consolidation. Split out of ``api/app.py`` so the
factory file stays under the ≤150-SLOC ceiling. The order of routers
matters when paths overlap, so the registration list is explicit and
stable rather than alphabetical.

Add a new router by importing it here and appending to ``ALL_ROUTERS``.
"""

from __future__ import annotations

from fastapi import FastAPI

from agent_memory_lite.api.routes import (
    audit_list,
    cold_candidates,
    cold_decisions,
    compact,
    decision_lineage,
    disputes,
    evals,
    explain_diff,
    feedback_summary,
    health,
    hygiene,
    ingest_episode,
    ingest_file,
    inter_agent,
    maintenance,
    memory_status,
    predictive_warnings,
    promote_to_behavior,
    propose_experiments,
    recurring_findings,
    references,
    research,
    review_queue,
    sentinel_trends,
    telemetry,
    ui,
    ui_brain,
    ui_brain_actions,
    ui_pages,
    ui_vendor,
    usage,
    workspaces,
)
from agent_memory_lite.api.routes import memory as memory_routes

ALL_ROUTERS = (
    health,
    # Canonical compact surface owns conflicting /memory/* names such as
    # /memory/search, /memory/pin, and /memory/archive.
    memory_routes,
    hygiene,
    ingest_episode,
    ingest_file,
    maintenance,
    research,
    usage,
    feedback_summary,
    cold_candidates,
    cold_decisions,
    explain_diff,
    decision_lineage,
    inter_agent,
    disputes,
    sentinel_trends,
    recurring_findings,
    workspaces,
    ui,
    ui_brain,
    ui_brain_actions,
    ui_pages,
    ui_vendor,
    compact,
    evals,
    references,
    audit_list,
    telemetry,
    memory_status,
    review_queue,
    promote_to_behavior,
    propose_experiments,
    predictive_warnings,
)


def register_all(app: FastAPI) -> None:
    """Wire every route module's router onto the FastAPI app."""
    for rt in ALL_ROUTERS:
        app.include_router(rt.router)
