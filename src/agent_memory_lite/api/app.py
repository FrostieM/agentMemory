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
    behavior,
    candidates,
    capabilities,
    capability_links,
    compact,
    context,
    decisions,
    evals,
    health,
    hygiene,
    ingest_episode,
    ingest_file,
    maintenance,
    research,
    search,
    task_state,
    theories,
    ui,
    usage,
)
from agent_memory_lite.config.local_only_guard import assert_local_only
from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
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
    app.include_router(task_state.router)
    app.include_router(theories.router)
    app.include_router(research.router)
    app.include_router(capabilities.router)
    app.include_router(usage.router)
    app.include_router(ui.router)
    app.include_router(compact.router)
    app.include_router(evals.router)
    return app
