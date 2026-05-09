"""2.0: GET /memory/code_overview — workspace dashboard payload.

Aggregates the v1.4-v1.8 code-memory substrate into one JSON
response the v2.0 ``/ui/code`` dashboard renders. Read-only.
Models live in ``code_overview_models.py``; SQL helpers in
``code_overview_db.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.code_overview_db import gather_counts, gather_top_called
from agent_memory_lite.api.routes.code_overview_models import (
    ActiveEditItem,
    BreakingItem,
    CodeOverviewResponse,
    RecentFile,
)
from agent_memory_lite.repositories.active_edits_repo import (
    cleanup_expired,
    list_active_claims,
)
from agent_memory_lite.repositories.file_digests_repo import list_digests
from agent_memory_lite.repositories.symbol_versions_breaking import (
    list_recent_signature_changes,
)

router = APIRouter()


@router.get("/memory/code_overview", response_model=CodeOverviewResponse)
def code_overview_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
    breaking_days: int = Query(default=7, ge=1, le=365),
    files_limit: int = Query(default=20, ge=1, le=200),
) -> CodeOverviewResponse:
    ensure_workspace_readable(workspace_id, settings)
    cleanup_expired(conn, workspace_id=workspace_id)

    counts = gather_counts(conn, workspace_id)
    digests = list_digests(conn, workspace_id=workspace_id, limit=files_limit)
    recent_files = [
        RecentFile(
            file_path=d.file_path,
            language=d.language,
            symbol_count=d.symbol_count,
            inbound_edge_count=d.inbound_edge_count,
            outbound_edge_count=d.outbound_edge_count,
            versions_recent=d.versions_recent,
            narrative=d.narrative,
            updated_at=d.updated_at,
        )
        for d in digests
    ]
    cutoff = (datetime.now(UTC) - timedelta(days=breaking_days)).isoformat()
    pairs = list_recent_signature_changes(
        conn, workspace_id=workspace_id, since_iso=cutoff, limit=20
    )
    breaking = [
        BreakingItem(
            qualified_name=cur.qualified_name,
            file_path=cur.file_path,
            prev_signature=prev.signature_text,
            new_signature=cur.signature_text,
            new_at=cur.created_at,
        )
        for prev, cur in pairs
    ]
    edits = list_active_claims(conn, workspace_id=workspace_id, limit=50)
    active_edits = [
        ActiveEditItem(
            qualified_name=e.qualified_name,
            file_path=e.file_path,
            agent_id=e.agent_id,
            expires_at=e.expires_at,
            note=e.note,
        )
        for e in edits
    ]
    return CodeOverviewResponse(
        workspace_id=workspace_id,
        counts=counts,
        recent_files=recent_files,
        breaking=breaking,
        active_edits=active_edits,
        top_called=gather_top_called(conn, workspace_id, limit=15),
    )
