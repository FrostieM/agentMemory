"""SQL operations for maintenance events."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEvent


def _json_dict(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _row_to_event(row: sqlite3.Row) -> MaintenanceEvent:
    return MaintenanceEvent(
        id=row["id"],
        workspace_id=row["workspace_id"],
        kind=row["kind"],
        severity=MaintenanceSeverity(row["severity"]),
        status=MaintenanceEventStatus(row["status"]),
        summary=row["summary"],
        details=_json_dict(row["details_json"]),
        source_episode_id=row["source_episode_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def insert_maintenance_event_row(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    workspace_id: str,
    kind: str,
    severity: MaintenanceSeverity,
    status: MaintenanceEventStatus,
    summary: str,
    details: dict[str, Any],
    source_episode_id: str | None,
    target_type: str | None,
    target_id: str | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO maintenance_events (
            id, workspace_id, kind, severity, status, summary, details_json,
            source_episode_id, target_type, target_id, created_at, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            event_id,
            workspace_id,
            kind,
            severity.value,
            status.value,
            summary,
            json.dumps(details, sort_keys=True, default=str),
            source_episode_id,
            target_type,
            target_id,
            created_at,
        ),
    )


def count_open_maintenance_events(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM maintenance_events
        WHERE workspace_id = ? AND status = 'open'
        """,
        (workspace_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def list_open_maintenance_events(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit: int = 20,
) -> list[MaintenanceEvent]:
    rows = conn.execute(
        """
        SELECT *
        FROM maintenance_events
        WHERE workspace_id = ? AND status = 'open'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (workspace_id, limit),
    ).fetchall()
    return [_row_to_event(row) for row in rows]
