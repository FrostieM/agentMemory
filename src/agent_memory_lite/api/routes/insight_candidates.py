"""GET /memory/insight_candidates — list pending lessons (v1.8).

Accept/reject actions live in ``insight_candidate_actions.py`` and are
mounted on the same router.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Query

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.insight_candidate_actions import (
    router as actions_router,
)
from agent_memory_lite.api.schemas.insight_candidates import (
    InsightCandidateResponse,
    ListInsightCandidatesResponse,
)

router = APIRouter()
router.include_router(actions_router)


def row_to_response(row: sqlite3.Row) -> InsightCandidateResponse:
    try:
        episode_ids = json.loads(str(row["source_episode_ids_json"] or "[]"))
    except json.JSONDecodeError:
        episode_ids = []
    try:
        tags = json.loads(str(row["tags_json"] or "[]"))
    except json.JSONDecodeError:
        tags = []
    return InsightCandidateResponse(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        insight_type=str(row["insight_type"]),
        summary=str(row["summary"]),
        proposed_action=str(row["proposed_action"]) if row["proposed_action"] else None,
        target_type=str(row["target_type"]) if row["target_type"] else None,
        target_id=str(row["target_id"]) if row["target_id"] else None,
        source_episode_ids=[str(item) for item in episode_ids if isinstance(item, str)],
        confidence=float(row["confidence"] or 0.0),
        status=str(row["status"]),
        promoted_insight_id=(
            str(row["promoted_insight_id"]) if row["promoted_insight_id"] else None
        ),
        tags=[str(item) for item in tags if isinstance(item, str)],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        decided_at=str(row["decided_at"]) if row["decided_at"] else None,
        decided_by=str(row["decided_by"]) if row["decided_by"] else None,
    )


@router.get("/memory/insight_candidates", response_model=ListInsightCandidatesResponse)
def list_insight_candidates_route(
    settings: SettingsDep,
    conn: DbDep,
    workspace_id: str = Query(default="default"),
    status: str | None = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=500),
) -> ListInsightCandidatesResponse:
    ensure_workspace_readable(workspace_id, settings)
    if status:
        rows = conn.execute(
            """
            SELECT * FROM insight_candidates
            WHERE workspace_id = ? AND status = ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (workspace_id, status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM insight_candidates
            WHERE workspace_id = ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    return ListInsightCandidatesResponse(
        workspace_id=workspace_id,
        candidates=[row_to_response(row) for row in rows],
    )
