"""Promote / reject actions for decision_candidates (v1.7).

Mounted on the same router as the list endpoint via include_router. Both
actions are operator-driven; the bridge never auto-promotes.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_writable
from agent_memory_lite.api.schemas.decision_candidates import (
    PromoteRequest,
    PromoteResponse,
    RejectRequest,
    RejectResponse,
)
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now

router = APIRouter()


def _load_pending(conn: sqlite3.Connection, *, workspace_id: str, candidate_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM decision_candidates WHERE id = ? AND workspace_id = ?",
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


@router.post(
    "/memory/decision_candidates/{candidate_id}/promote",
    response_model=PromoteResponse,
)
def promote_decision_candidate_route(
    candidate_id: str,
    body: PromoteRequest,
    settings: SettingsDep,
    conn: DbDep,
) -> PromoteResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    row = _load_pending(conn, workspace_id=body.workspace_id, candidate_id=candidate_id)
    decision = write_decision(
        conn,
        DecisionIn(
            workspace_id=body.workspace_id,
            title=body.title_override or str(row["proposed_title"]),
            decision_text=body.decision_text_override or str(row["proposed_decision_text"]),
            rationale=body.rationale_override or str(row["proposed_rationale"] or ""),
            confidence=float(row["confidence"] or 0.7),
        ),
        settings=settings,
    )
    now_iso = iso_now()
    conn.execute(
        """
        UPDATE decision_candidates
        SET status = 'promoted', promoted_decision_id = ?, decided_at = ?,
            decided_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (decision.id, now_iso, body.decided_by, now_iso, candidate_id),
    )
    insert_audit(
        conn,
        workspace_id=body.workspace_id,
        action="decision_candidate.promoted",
        target_type="decision_candidate",
        target_id=candidate_id,
        after={"decision_id": decision.id, "decided_by": body.decided_by},
    )
    conn.commit()
    return PromoteResponse(candidate_id=candidate_id, decision_id=decision.id, status="promoted")


@router.post(
    "/memory/decision_candidates/{candidate_id}/reject",
    response_model=RejectResponse,
)
def reject_decision_candidate_route(
    candidate_id: str,
    body: RejectRequest,
    settings: SettingsDep,
    conn: DbDep,
) -> RejectResponse:
    ensure_workspace_writable(body.workspace_id, settings)
    _load_pending(conn, workspace_id=body.workspace_id, candidate_id=candidate_id)
    now_iso = iso_now()
    conn.execute(
        """
        UPDATE decision_candidates
        SET status = 'rejected', decided_at = ?, decided_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (now_iso, body.decided_by, now_iso, candidate_id),
    )
    insert_audit(
        conn,
        workspace_id=body.workspace_id,
        action="decision_candidate.rejected",
        target_type="decision_candidate",
        target_id=candidate_id,
        after={"decided_by": body.decided_by, "reason": body.reason},
    )
    conn.commit()
    return RejectResponse(candidate_id=candidate_id, status="rejected")
