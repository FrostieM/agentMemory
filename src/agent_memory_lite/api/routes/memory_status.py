"""GET /memory/status — one-shot coverage + adoption snapshot.

Single endpoint an agent can hit to answer "is this workspace
indexed?" + "is my discipline showing in the data?" without paging
through hygiene / quality_gate / health separately.
Read-only, no embedding model touched, sub-100ms target.
SQL helpers live in ``memory_status_queries.py``.

Pass ``include_environment=true`` to also surface server anchor /
registry / hub-mode info — closes the silent-misconfig footgun
identified in the 2026-05-19 routing-bug post-mortem.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Query

from agent_memory_lite import __version__
from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.active_memory_status import build_active_memory
from agent_memory_lite.api.routes.memory_status_queries import (
    gather_adoption,
    gather_code_counts,
    gather_degradation,
    gather_memory_counts,
    max_ts,
    recent_actions_7d,
)
from agent_memory_lite.api.schemas.memory_status import (
    EnvironmentInfo,
    MemoryStatusResponse,
)
from agent_memory_lite.api.workspace_routing import ensure_workspace_readable_db
from agent_memory_lite.config.offline_bootstrap import hf_offline_active
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.config.workspace_registry import WorkspaceRegistry

router = APIRouter()


def build_environment(conn: sqlite3.Connection, settings: Settings) -> EnvironmentInfo:
    """Shared helper used by the HTTP route + the MCP local-fallback handler.

    Defensive try/excepts around the registry + migrations queries —
    the diagnostic endpoint must NEVER 500 (audit-round-2 finding A#3).
    Corrupt JSON / missing file / unmigrated DB all degrade to an
    explicit-empty payload so the operator can still see the anchor +
    hub_mode info even when something downstream is broken.

    P2 extension: surface embedding/vector/llm backend + HTTP base URL
    so the agent sees the full runtime stack in one call (closes
    audit-round-1 finding A#6).
    """
    try:
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        migrations = [str(row[0]) for row in rows]
    except sqlite3.OperationalError:
        migrations = []
    registry_path = str(settings.workspaces_file)
    workspaces: list[str] = []
    registry_load_error: str | None = None
    try:
        registry = WorkspaceRegistry(settings.workspaces_file)
        registry_path = str(registry.path)
        workspaces = sorted(entry.id for entry in registry.list())
        # Distinguish a CORRUPT/unreadable registry (load error set) from a
        # genuinely empty/absent one. Without this, a broken registry looks like
        # "0 workspaces registered" on the diagnostic surface -- masking the root
        # cause of mis-routing/anchor-fallthrough (Batch C, read-isolation).
        registry_load_error = registry.last_load_error
    except Exception:  # pragma: no cover - defensive against corrupt JSON
        # Registry construction/list itself raised — surface a generic corrupt
        # marker so the diagnostic shows "registry unavailable", not empty.
        registry_load_error = "corrupt:unknown"
    return EnvironmentInfo(
        hub_mode=bool(settings.hub_mode),
        anchor_workspace_id=settings.workspace_id,
        anchor_db_path=str(settings.db_path),
        anchor_vector_db_path=str(settings.vector_db_path),
        forbid_default_workspace=bool(settings.forbid_default_workspace),
        strict_workspace_isolation=bool(settings.strict_workspace_isolation),
        registry_path=registry_path,
        registry_workspaces=workspaces,
        registry_load_error=registry_load_error,
        applied_migrations=migrations,
        embedding_backend=str(getattr(settings, "embedding_backend", "") or ""),
        embedding_model=str(getattr(settings, "embedding_model", "") or ""),
        vector_backend=str(getattr(settings, "vector_backend", "") or ""),
        llm_backend=str(getattr(settings, "llm_backend", "") or ""),
        llm_model=str(getattr(settings, "llm_model", "") or ""),
        http_base_url=f"http://127.0.0.1:{int(getattr(settings, 'api_port', 8765))}",
        hf_auto_offline=bool(getattr(settings, "hf_auto_offline", False)),
        hf_offline_active=hf_offline_active(),
    )


@router.get("/memory/status", response_model=MemoryStatusResponse)
def memory_status_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
    include_environment: bool = Query(default=False),
    include_active_memory: bool = Query(default=False),
) -> MemoryStatusResponse:
    """One-shot status. ``include_active_memory=true`` adds v3.1
    vector counts (open proposals, predictive warnings, blindspots)."""
    ensure_workspace_readable(workspace_id, settings)
    # Read-isolation guard (Batch C): /memory/status is the v3 tool an operator hits
    # to diagnose mis-routing, so it must refuse a definite cross-DB mismatch like
    # its 8 sibling read endpoints + its MCP twin -- otherwise it would report a
    # foreign/co-resident DB's counts as this workspace's, masking the mismatch.
    ensure_workspace_readable_db(conn, workspace_id, settings)
    environment = build_environment(conn, settings) if include_environment else None
    active_memory = build_active_memory(conn, workspace_id) if include_active_memory else None
    return MemoryStatusResponse(
        version=__version__,
        workspace_id=workspace_id,
        memory=gather_memory_counts(conn, workspace_id),
        code_memory=gather_code_counts(conn, workspace_id),
        adoption=gather_adoption(conn, workspace_id),
        last_episode_at=max_ts(
            conn, "SELECT MAX(created_at) FROM episodes WHERE workspace_id=?", workspace_id
        ),
        last_decision_at=max_ts(
            conn, "SELECT MAX(updated_at) FROM decisions WHERE workspace_id=?", workspace_id
        ),
        last_ingest_file_at=max_ts(
            conn,
            "SELECT MAX(created_at) FROM audit_log WHERE workspace_id=? AND action='ingest_file'",
            workspace_id,
        ),
        recent_actions_7d=recent_actions_7d(conn, workspace_id),
        degradation=gather_degradation(conn, workspace_id),
        environment=environment,
        active_memory=active_memory,
    )
