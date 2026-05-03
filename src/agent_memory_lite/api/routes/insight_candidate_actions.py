"""Accept / reject for insight_candidates (v1.8).

Accept calls the canonical ``distill_insight`` path so the research_insights
row is created with the same shape as any operator-distilled insight; the
candidate row is then flipped to status='accepted' with promoted_insight_id.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, HTTPException

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.schemas.insight_candidates import (
    InsightCandidateAcceptRequest,
    InsightCandidateAcceptResponse,
    InsightCandidateRejectRequest,
    InsightCandidateRejectResponse,
)
from agent_memory_lite.ingestion.research_writer_insights import distill_insight
from agent_memory_lite.models.enums import InsightStatus, InsightType
from agent_memory_lite.models.research import ResearchInsightIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now

router = APIRouter()


def _load_pending(conn: sqlite3.Connection, *, workspace_id: str, candidate_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM insight_candidates WHERE id = ? AND workspace_id = ?",
        (candidate_id, workspace_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"candidate {candidate_id!r} not found")
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"candidate already decided (status={row['status']!r})",
        )
    return row


def _decoded_episode_ids(row: sqlite3.Row) -> list[str]:
    try:
        decoded = json.loads(str(row["source_episode_ids_json"] or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded]


@router.post(
    "/memory/insight_candidates/{candidate_id}/accept",
    response_model=InsightCandidateAcceptResponse,
)
def accept_insight_candidate_route(
    candidate_id: str,
    body: InsightCandidateAcceptRequest,
    settings: SettingsDep,
    conn: DbDep,
) -> InsightCandidateAcceptResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    row = _load_pending(conn, workspace_id=body.workspace_id, candidate_id=candidate_id)
    insight_type = InsightType(str(row["insight_type"]))
    payload = ResearchInsightIn(
        workspace_id=body.workspace_id,
        insight_type=insight_type,
        summary=body.summary_override or str(row["summary"]),
        proposed_action=body.proposed_action_override or row["proposed_action"],
        target_type=body.target_type or (str(row["target_type"]) if row["target_type"] else None),
        target_id=body.target_id or (str(row["target_id"]) if row["target_id"] else None),
        source_episode_ids=_decoded_episode_ids(row),
        confidence=float(row["confidence"] or 0.6),
        status=InsightStatus.NEW,
        tags=[],
    )
    insight = distill_insight(conn, payload)
    now_iso = iso_now()
    conn.execute(
        """
        UPDATE insight_candidates
        SET status = 'accepted', promoted_insight_id = ?, decided_at = ?,
            decided_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (insight.id, now_iso, body.decided_by, now_iso, candidate_id),
    )
    insert_audit(
        conn,
        workspace_id=body.workspace_id,
        action="insight_candidate.accepted",
        target_type="insight_candidate",
        target_id=candidate_id,
        after={"insight_id": insight.id, "decided_by": body.decided_by},
    )
    conn.commit()
    return InsightCandidateAcceptResponse(
        candidate_id=candidate_id, insight_id=insight.id, status="accepted"
    )


@router.post(
    "/memory/insight_candidates/{candidate_id}/reject",
    response_model=InsightCandidateRejectResponse,
)
def reject_insight_candidate_route(
    candidate_id: str,
    body: InsightCandidateRejectRequest,
    settings: SettingsDep,
    conn: DbDep,
) -> InsightCandidateRejectResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    _load_pending(conn, workspace_id=body.workspace_id, candidate_id=candidate_id)
    now_iso = iso_now()
    conn.execute(
        """
        UPDATE insight_candidates
        SET status = 'rejected', decided_at = ?, decided_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (now_iso, body.decided_by, now_iso, candidate_id),
    )
    insert_audit(
        conn,
        workspace_id=body.workspace_id,
        action="insight_candidate.rejected",
        target_type="insight_candidate",
        target_id=candidate_id,
        after={"decided_by": body.decided_by, "reason": body.reason},
    )
    conn.commit()
    return InsightCandidateRejectResponse(candidate_id=candidate_id, status="rejected")
