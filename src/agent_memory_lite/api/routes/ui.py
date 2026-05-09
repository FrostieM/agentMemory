"""Local browser UI for inspecting memory as a live graph.

Static specs (groups, tables, process stages) live in ``ui_specs.py``;
SQL helpers live in ``ui_db.py``; graph builder in ``ui_graph.py``;
process-stage view in ``ui_process.py``; workspace listing in
``ui_workspaces.py``. This module owns the four routes plus the
maintenance-warning fan-out.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.ui_db import table_exists
from agent_memory_lite.api.routes.ui_graph import build_graph
from agent_memory_lite.api.routes.ui_process import build_process, signature
from agent_memory_lite.api.routes.ui_workspaces import (
    available_workspaces,
    registered_workspaces,
)
from agent_memory_lite.api.ui_telemetry import event_stream, ui_telemetry
from agent_memory_lite.models.enums import MaintenanceEventStatus
from agent_memory_lite.repositories.maintenance_repo import list_maintenance_events
from agent_memory_lite.utils.time import iso_now

router = APIRouter(include_in_schema=False)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_UI_ROOT = _PACKAGE_ROOT / "ui"
_ASSETS = {
    "app.js": "application/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    # 2.0: code-memory dashboard. Served at /ui/code.html alongside the
    # legacy index.html so the user can land directly on the code view.
    "code.html": "text/html; charset=utf-8",
    # 2.1.2: D3 force-directed graph dashboard.
    "graph.html": "text/html; charset=utf-8",
}

_NO_CACHE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _serve_html(filename: str) -> FileResponse:
    return FileResponse(
        _UI_ROOT / filename, media_type="text/html; charset=utf-8", headers=_NO_CACHE
    )


@router.get("/ui")
def memory_ui_index() -> FileResponse:
    return _serve_html("index.html")


@router.get("/ui/code")
def memory_ui_code() -> FileResponse:
    """2.0 dashboard backed by /memory/code_overview."""
    return _serve_html("code.html")


@router.get("/ui/graph")
def memory_ui_graph() -> FileResponse:
    """2.1.2 D3 graph dashboard backed by /memory/code_graph."""
    return _serve_html("graph.html")


@router.get("/ui/{asset_name}")
def memory_ui_asset(asset_name: str) -> FileResponse:
    if asset_name not in _ASSETS:
        return FileResponse(
            _UI_ROOT / "index.html",
            media_type="text/html; charset=utf-8",
            headers=_NO_CACHE,
        )
    return FileResponse(
        _UI_ROOT / asset_name,
        media_type=_ASSETS[asset_name],
        headers=_NO_CACHE,
    )


# 2.2 (Phase 2.7): vendor-asset routes live in api/routes/ui_vendor.py
# so this module stays under the ≤150-SLOC ceiling. The router is
# registered alongside ours via api/__init__.py.


def _maintenance_warnings(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "maintenance_events"):
        return []
    events = list_maintenance_events(
        conn,
        workspace_id=workspace_id,
        statuses=[MaintenanceEventStatus.OPEN],
        limit=limit,
    )
    return [
        {
            "event_id": event.id,
            "kind": event.kind,
            "severity": event.severity.value,
            "status": event.status.value,
            "summary": event.summary,
            "details": event.details,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "created_at": event.created_at,
        }
        for event in events
    ]


@router.get("/memory/ui/state")
def memory_ui_state(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str | None = Query(default=None),
    recent_limit: int = Query(default=80, ge=1, le=100),
) -> dict[str, Any]:
    selected_workspace = workspace_id or settings.workspace_id
    ensure_workspace_readable(selected_workspace, settings)
    nodes, edges, counts, recent = build_graph(
        conn, workspace_id=selected_workspace, recent_limit=recent_limit
    )
    process = build_process(conn, workspace_id=selected_workspace, counts=counts, recent=recent)
    latest_events = ui_telemetry.snapshot(workspace_id=selected_workspace, limit=80)
    graph_deltas = ui_telemetry.graph_deltas(workspace_id=selected_workspace, limit=50)
    active_requests = ui_telemetry.active_requests(workspace_id=selected_workspace)
    return {
        "status": "ok",
        "workspace_id": selected_workspace,
        "workspaces": available_workspaces(conn, settings),
        "registered_workspaces": registered_workspaces(settings, selected_workspace),
        "hub_mode": settings.hub_mode,
        "generated_at": iso_now(),
        "db_path": str(settings.db_path),
        "vector_path": str(settings.vector_db_path),
        "counts": counts,
        "warnings": _maintenance_warnings(conn, workspace_id=selected_workspace),
        "graph": {"nodes": nodes, "edges": edges},
        "process": process,
        "recent": recent,
        "latest_events": latest_events,
        "graph_deltas": graph_deltas,
        "active_requests": active_requests,
        "signature": signature(counts, recent),
    }


@router.get("/memory/ui/events")
def memory_ui_events(
    settings: SettingsDep,
    workspace_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    once: bool = Query(default=False),
) -> StreamingResponse:
    selected_workspace = workspace_id or settings.workspace_id
    ensure_workspace_readable(selected_workspace, settings)
    return StreamingResponse(
        event_stream(workspace_id=selected_workspace, since=since, once=once),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
