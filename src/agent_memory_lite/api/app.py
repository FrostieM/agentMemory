"""FastAPI application factory.

`create_app` runs the local-only guard, applies any pending migrations against
the configured database, probes the LLM provider (unless skipped), registers
exception handlers, and wires the route modules.

Phase 3.3 of v2.2: route registration moved to ``app_routes.py`` so this
factory stays under the ≤150-SLOC ceiling.
"""

from __future__ import annotations

from fastapi import FastAPI

from agent_memory_lite.api.agent_identity_middleware import AgentIdentityMiddleware
from agent_memory_lite.api.app_routes import register_all
from agent_memory_lite.api.auth import install_api_token_guard
from agent_memory_lite.api.errors import install_handlers
from agent_memory_lite.api.origin_guard_middleware import OriginGuardMiddleware
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
    # v3.5 sector-6+7 audit-followup: refuse browser requests whose
    # Origin / Host header doesn't point at loopback. Defeats DNS-
    # rebinding + cross-site form-POST attacks against the local
    # service. curl / httpx / inject hooks pass loopback unchanged.
    # Operator opts out with MEMORY_ALLOW_REMOTE_ORIGIN=1.
    app.add_middleware(OriginGuardMiddleware)
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
    register_all(app)
    return app
