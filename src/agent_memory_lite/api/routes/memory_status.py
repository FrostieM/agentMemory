"""GET /memory/status — one-shot coverage + adoption snapshot.

Single endpoint an agent can hit to answer "is this workspace
indexed?" + "is my discipline showing in the data?" without paging
through hygiene / quality_gate / health / code_overview separately.
Read-only, no embedding model touched, sub-100ms target.
SQL helpers live in ``memory_status_queries.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from agent_memory_lite import __version__
from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.memory_status_queries import (
    gather_adoption,
    gather_code_counts,
    gather_memory_counts,
    max_ts,
    recent_actions_7d,
)
from agent_memory_lite.api.schemas.memory_status import MemoryStatusResponse

router = APIRouter()


@router.get("/memory/status", response_model=MemoryStatusResponse)
def memory_status_route(
    conn: DbDep,
    settings: SettingsDep,
    workspace_id: str = Query(default="default"),
) -> MemoryStatusResponse:
    ensure_workspace_readable(workspace_id, settings)
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
    )
