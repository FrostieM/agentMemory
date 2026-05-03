"""GET /memory/recurring_findings — events past the recurrence threshold (v1.9).

Distinct from /memory/list_maintenance_events which returns all events.
This endpoint surfaces only events whose recurrence_count >= the
configured threshold, so the operator sees recurring issues first.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable

router = APIRouter()


class RecurringFindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    workspace_id: str
    kind: str
    severity: str
    status: str
    summary: str
    target_type: str | None
    target_id: str | None
    recurrence_count: int
    first_seen_at: str | None
    last_seen_at: str | None
    created_at: str
    details: dict[str, object]


class RecurringFindingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    threshold: int
    findings: list[RecurringFindingResponse]


def _row_to_response(row: sqlite3.Row) -> RecurringFindingResponse:
    try:
        details = json.loads(str(row["details_json"] or "{}"))
    except json.JSONDecodeError:
        details = {}
    if not isinstance(details, dict):
        details = {}
    return RecurringFindingResponse(
        event_id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        kind=str(row["kind"]),
        severity=str(row["severity"]),
        status=str(row["status"]),
        summary=str(row["summary"]),
        target_type=str(row["target_type"]) if row["target_type"] else None,
        target_id=str(row["target_id"]) if row["target_id"] else None,
        recurrence_count=int(row["recurrence_count"] or 1),
        first_seen_at=str(row["first_seen_at"]) if row["first_seen_at"] else None,
        last_seen_at=str(row["last_seen_at"]) if row["last_seen_at"] else None,
        created_at=str(row["created_at"]),
        details=details,
    )


@router.get("/memory/recurring_findings", response_model=RecurringFindingsResponse)
def recurring_findings_route(
    settings: SettingsDep,
    conn: DbDep,
    workspace_id: str = Query(default="default"),
    threshold: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=1000),
) -> RecurringFindingsResponse:
    ensure_workspace_readable(workspace_id, settings)
    effective_threshold = threshold or settings.recurrence_threshold
    rows = conn.execute(
        """
        SELECT id, workspace_id, kind, severity, status, summary, details_json,
               target_type, target_id, recurrence_count, first_seen_at,
               last_seen_at, created_at
        FROM maintenance_events
        WHERE workspace_id = ?
          AND status = 'open'
          AND recurrence_count >= ?
        ORDER BY recurrence_count DESC, last_seen_at DESC
        LIMIT ?
        """,
        (workspace_id, effective_threshold, limit),
    ).fetchall()
    return RecurringFindingsResponse(
        workspace_id=workspace_id,
        threshold=effective_threshold,
        findings=[_row_to_response(row) for row in rows],
    )
