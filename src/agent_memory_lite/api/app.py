"""FastAPI application factory.

`create_app` runs the local-only guard, applies any pending migrations against
the configured database, probes the LLM provider (unless skipped), registers
exception handlers, and wires the route modules.
"""

from __future__ import annotations

from fastapi import FastAPI

from agent_memory_lite.api.agent_identity_middleware import AgentIdentityMiddleware
from agent_memory_lite.api.auth import install_api_token_guard
from agent_memory_lite.api.errors import install_handlers
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
    pin,
    promote_to_behavior,
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
    ui_vendor,
    usage,
    workspaces,
)
from agent_memory_lite.api.workspace_routing_middleware import WorkspaceRoutingMiddleware
from agent_memory_lite.config.local_only_guard import assert_local_only
from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.integrity_check import record_foreign_key_violations
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.extraction.llm_extractor import probe_ollama
from agent_memory_lite.repositories.workspace_manifest_repo import ensure_workspace_manifest
from agent_memory_lite.version import __version__


def _bootstrap(settings: Settings) -> None:
    assert_local_only(settings)
    conn = open_connection(settings.db_path)
    try:
        apply_migrations(conn)
        if settings.enforce_workspace_manifest:
            ensure_workspace_manifest(
                conn,
                workspace_id=settings.workspace_id,
                allow_default_workspace=not settings.forbid_default_workspace,
            )
        # Surface foreign-key drift as a maintenance event so the
        # operator sees it in /health and the hygiene report instead
        # of finding silently-dangling rows months later. Cheap walk;
        # no-op when there's nothing to report.
        record_foreign_key_violations(conn, workspace_id=settings.workspace_id)
    finally:
        close_connection(conn)
    probe_ollama(settings)


def _register_code_routers(app: FastAPI) -> None:
    """1.7.0: code-memory routers (v1.4 → v1.7) grouped here so
    create_app stays under the per-function statement ceiling."""
    app.include_router(find_symbols.router)
    app.include_router(graph_neighbors.router)
    app.include_router(symbol_history.router)
    app.include_router(breaking_changes.router)
    app.include_router(active_edits.router)
    app.include_router(soft_neighbors.router)
    app.include_router(file_digests.router)
    app.include_router(code_overview.router)
    app.include_router(code_graph.router)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _bootstrap(settings)

    app = FastAPI(
        title="agent-memory-lite",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )
    install_handlers(app)
    install_api_token_guard(app, settings)
    # Hub mode: route /memory/* requests to the workspace_id's own DB
    # automatically when the caller did not pass an explicit
    # X-Memory-DB-Path header. No-op when hub_mode is off, so project
    # mode still uses the anchor DB unchanged.
    app.add_middleware(WorkspaceRoutingMiddleware, settings=settings)
    # 1.3.0: stash X-Memory-Agent-Id in a request-scoped ContextVar
    # so insert_audit can attribute every mutating call to its agent
    # client. Mounted AFTER routing so the workspace_id is already
    # decided when the audit row is written.
    app.add_middleware(AgentIdentityMiddleware)
    app.include_router(health.router)
    app.include_router(hygiene.router)
    app.include_router(behavior.router)
    app.include_router(candidates.router)
    app.include_router(capability_links.router)
    app.include_router(ingest_episode.router)
    app.include_router(ingest_file.router)
    app.include_router(maintenance.router)
    app.include_router(search.router)
    app.include_router(context.router)
    app.include_router(decisions.router)
    app.include_router(get_object.router)
    app.include_router(task_state.router)
    app.include_router(theories.router)
    app.include_router(research.router)
    app.include_router(capabilities.router)
    app.include_router(usage.router)
    app.include_router(feedback_summary.router)
    app.include_router(cold_candidates.router)
    app.include_router(cold_decisions.router)
    app.include_router(explain_diff.router)
    app.include_router(decision_lineage.router)
    app.include_router(decision_candidates.router)
    app.include_router(insight_candidates.router)
    app.include_router(sentinel_trends.router)
    app.include_router(recurring_findings.router)
    app.include_router(workspaces.router)
    app.include_router(ui.router)
    app.include_router(ui_vendor.router)
    app.include_router(compact.router)
    app.include_router(evals.router)
    app.include_router(archive.router)
    app.include_router(pin.router)
    app.include_router(references.router)
    app.include_router(audit_list.router)
    app.include_router(telemetry.router)
    app.include_router(memory_state_snapshots.router)
    app.include_router(review_queue.router)
    app.include_router(promote_to_behavior.router)
    _register_code_routers(app)
    return app
