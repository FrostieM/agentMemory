"""GET /memory/decision_candidates — list pending proposals (v1.7).

Promote and reject actions live in ``decision_candidate_actions.py`` and
are mounted on the same router so the URL prefix is shared.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Query

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable
from agent_memory_lite.api.routes.decision_candidate_actions import (
    router as actions_router,
)
from agent_memory_lite.api.schemas.decision_candidates import (
    DecisionCandidateResponse,
    ListDecisionCandidatesResponse,
)

router = APIRouter()
router.include_router(actions_router)


def row_to_response(row: sqlite3.Row) -> DecisionCandidateResponse:
    return DecisionCandidateResponse(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        theory_id=str(row["theory_id"]),
        proposed_title=str(row["proposed_title"]),
        proposed_decision_text=str(row["proposed_decision_text"]),
        proposed_rationale=str(row["proposed_rationale"]) if row["proposed_rationale"] else None,
        evidence_count=int(row["evidence_count"] or 0),
        evidence_strength=float(row["evidence_strength"] or 0.0),
        confidence=float(row["confidence"] or 0.0),
        status=str(row["status"]),
        promoted_decision_id=(
            str(row["promoted_decision_id"]) if row["promoted_decision_id"] else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        decided_at=str(row["decided_at"]) if row["decided_at"] else None,
        decided_by=str(row["decided_by"]) if row["decided_by"] else None,
    )


@router.get("/memory/decision_candidates", response_model=ListDecisionCandidatesResponse)
def list_decision_candidates_route(
    settings: SettingsDep,
    conn: DbDep,
    workspace_id: str = Query(default="default"),
    status: str | None = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=500),
) -> ListDecisionCandidatesResponse:
    ensure_workspace_readable(workspace_id, settings)
    if status:
        rows = conn.execute(
            """
            SELECT * FROM decision_candidates
            WHERE workspace_id = ? AND status = ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (workspace_id, status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM decision_candidates
            WHERE workspace_id = ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    return ListDecisionCandidatesResponse(
        workspace_id=workspace_id,
        candidates=[row_to_response(row) for row in rows],
    )
