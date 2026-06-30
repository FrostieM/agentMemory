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
from agent_memory_lite.api.body_size_limit_middleware import BodySizeLimitMiddleware
from agent_memory_lite.api.errors import install_handlers
from agent_memory_lite.api.origin_guard_middleware import OriginGuardMiddleware
from agent_memory_lite.api.security_middleware import SecurityMiddleware
from agent_memory_lite.api.workspace_routing_middleware import WorkspaceRoutingMiddleware
from agent_memory_lite.config.local_only_guard import assert_local_only
from agent_memory_lite.config.offline_bootstrap import maybe_configure_offline
from agent_memory_lite.config.settings import Settings, get_settings
from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.integrity_check import record_foreign_key_violations
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.extraction.llm_extractor import probe_ollama
from agent_memory_lite.repositories.workspace_manifest_repo import ensure_workspace_manifest
from agent_memory_lite.version import __version__


def _bootstrap(settings: Settings) -> None:
    assert_local_only(settings)
    # Default HF to offline once the embedding model is cached -- before any
    # request triggers the lazy model load. No-op when MEMORY_HF_AUTO_OFFLINE
    # is false or the model is not yet cached (allows one-time bootstrap).
    maybe_configure_offline(settings)
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
    # ORDERING (global-audit 2026-06-30): Starlette runs the LAST-added middleware
    # OUTERMOST (first on the inbound path), so we register INNERMOST -> OUTERMOST.
    # The intended inbound order is:
    #   Security -> OriginGuard -> BodySizeLimit -> WorkspaceRouting -> AgentIdentity
    # The previous code was written top-to-bottom as if the FIRST-added were
    # outermost, which inverted every layer -- most critically it let
    # WorkspaceRouting drain an UNBOUNDED request body (it buffers the body to read
    # workspace_id) BEFORE BodySizeLimit could cap it, bypassing the DoS guard in
    # hub mode. Registering in reverse fixes that and makes Security genuinely
    # outermost so its headers wrap the inner 413/403/415 responses.

    # innermost: stash X-Memory-Agent-Id in a request-scoped ContextVar AFTER
    # routing has decided the workspace, so insert_audit attributes every mutating
    # call to its agent client.
    app.add_middleware(AgentIdentityMiddleware)
    # Hub mode: route /memory/* to the workspace_id's own DB when the caller did not
    # pass an explicit X-Memory-DB-Path header. It DRAINS+replays the body to read
    # workspace_id, so it must sit INSIDE BodySizeLimit (below) -- that drain is then
    # already byte-capped. No-op when hub_mode is off.
    app.add_middleware(WorkspaceRoutingMiddleware, settings=settings)
    # Hard-cap request body size BEFORE workspace routing drains it. No auth by
    # default + a 127.0.0.1 bind means a careless tool loop or a same-host attacker
    # could otherwise POST GBs and pin the embedding worker or fill the WAL. Default
    # 10 MB covers legitimate code/episode ingest; raise via env for larger docs.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    # Refuse browser requests whose Origin / Host header doesn't point at loopback.
    # Defeats DNS-rebinding + cross-site form-POST attacks against the local service.
    # curl / httpx / inject hooks pass loopback unchanged. Opt out with
    # MEMORY_ALLOW_REMOTE_ORIGIN=1.
    app.add_middleware(OriginGuardMiddleware)
    # outermost: add CSP / X-Frame-Options / X-Content-Type-Options to EVERY response
    # (including the inner middlewares' 413 / 403 / 415) and reject non-JSON POSTs to
    # /memory/* -- a by-design CSRF guard since a browser <form> cannot send
    # Content-Type: application/json. Registered LAST so it is outermost.
    app.add_middleware(SecurityMiddleware)
    register_all(app)
    return app
