"""FastAPI application factory.

`create_app` runs the local-only guard, applies any pending migrations against
the configured database, probes the LLM provider (unless skipped), registers
exception handlers, and wires the route modules.
"""

from __future__ import annotations

from fastapi import FastAPI

from agent_memory_lite.api.auth import install_api_token_guard
from agent_memory_lite.api.errors import install_handlers
from agent_memory_lite.api.routes import (
    archive,
    audit_list,
    behavior,
    candidates,
    capabilities,
    capability_links,
    compact,
    context,
    decisions,
    evals,
    get_object,
    health,
    hygiene,
    ingest_episode,
    ingest_file,
    maintenance,
    memory_state_snapshots,
    pin,
    references,
    research,
    review_queue,
    search,
    task_state,
    theories,
    ui,
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
    app.include_router(workspaces.router)
    app.include_router(ui.router)
    app.include_router(compact.router)
    app.include_router(evals.router)
    app.include_router(archive.router)
    app.include_router(pin.router)
    app.include_router(references.router)
    app.include_router(audit_list.router)
    app.include_router(memory_state_snapshots.router)
    app.include_router(review_queue.router)
    return app
