"""Read-only queries for maintenance events.

Split out of ``maintenance_repo.py`` so the repo file stays under the
SLOC ceiling and the read path is easy to find independently from the
INSERT / UPDATE plumbing.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEvent


def _json_dict(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def row_to_event(row: sqlite3.Row) -> MaintenanceEvent:
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
    return [row_to_event(row) for row in rows]


def list_maintenance_events(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    statuses: list[MaintenanceEventStatus] | None = None,
    limit: int = 20,
) -> list[MaintenanceEvent]:
    clauses = ["workspace_id = ?"]
    params: list[str | int] = [workspace_id]
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(status.value for status in statuses)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT *
        FROM maintenance_events
        WHERE {" AND ".join(clauses)}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [row_to_event(row) for row in rows]
