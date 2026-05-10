"""Local browser UI for inspecting memory as a live graph.

Static specs (groups, tables, process stages) live in ``ui_specs.py``;
SQL helpers live in ``ui_db.py``; graph builder in ``ui_graph.py``;
process-stage view in ``ui_process.py``; workspace listing in
``ui_workspaces.py``. This module owns the four routes plus the
maintenance-warning fan-out.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

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

# 2.2 (Phase 3.3): static HTML/asset pages and vendor bundles live in
# their own modules so this file stays under the ≤150-SLOC ceiling.
# All three routers are registered alongside in api/app.py:
#
#   ui_pages.router    — /ui /ui/code /ui/graph /ui/review /ui/{asset}
#   ui_vendor.router   — /ui/vendor/{asset}
#   ui.router          — /memory/ui/state SSE + helpers (this file)


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
