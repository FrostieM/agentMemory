"""Read-only queries for maintenance events.

Split out of ``maintenance_repo.py`` so the repo file stays under the
SLOC ceiling and the read path is easy to find independently from the
INSERT / UPDATE plumbing.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import (
    MaintenanceActionStatus,
    MaintenanceEventStatus,
    MaintenanceSeverity,
    coerce_enum,
)
from agent_memory_lite.models.maintenance import MaintenanceEvent


def _json_dict(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _action_status(row: sqlite3.Row) -> MaintenanceActionStatus:
    """Read ``action_status`` from a row that may predate migration 0036.

    On a freshly-migrated DB every column is populated by the ALTER TABLE
    default ('open'), but a sqlite3.Row from a test fixture or an
    in-memory DB that never ran the migration would raise IndexError.
    Defaulting to OPEN keeps the read path resilient."""
    try:
        raw = row["action_status"]
    except (IndexError, KeyError):
        return MaintenanceActionStatus.OPEN
    if not raw:
        return MaintenanceActionStatus.OPEN
    # v3.5 audit-followup: drift-tolerant — unknown value degrades to OPEN
    # so the queue page never 500s on a stray status string.
    return coerce_enum(MaintenanceActionStatus, raw, MaintenanceActionStatus.OPEN)


def _opt_col(row: sqlite3.Row, name: str) -> str | None:
    """Return ``row[name]`` or None when the column doesn't exist
    (same migration-resilience rationale as ``_action_status``)."""
    try:
        value = row[name]
    except (IndexError, KeyError):
        return None
    return None if value is None else str(value)


def row_to_event(row: sqlite3.Row) -> MaintenanceEvent:
    # v3.5 audit-followup: tolerate unknown severity / status strings so
    # /ui/queue + /health + hygiene reports degrade gracefully instead
    # of 500ing the whole route on a single rogue row.
    return MaintenanceEvent(
        id=row["id"],
        workspace_id=row["workspace_id"],
        kind=row["kind"],
        severity=coerce_enum(MaintenanceSeverity, row["severity"], MaintenanceSeverity.WARNING),
        status=coerce_enum(MaintenanceEventStatus, row["status"], MaintenanceEventStatus.OPEN),
        summary=row["summary"],
        details=_json_dict(row["details_json"]),
        source_episode_id=row["source_episode_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        action_status=_action_status(row),
        assigned_to=_opt_col(row, "assigned_to"),
        action_notes=_opt_col(row, "action_notes"),
        claimed_at=_opt_col(row, "claimed_at"),
        dismissed_at=_opt_col(row, "dismissed_at"),
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
    action_statuses: list[MaintenanceActionStatus] | None = None,
    limit: int = 20,
) -> list[MaintenanceEvent]:
    """List maintenance events filtered by substrate ``status`` and/or
    operator-triage ``action_status``. v3.4 #6 adds ``action_statuses``
    so the /ui/queue page can filter by triage state without losing the
    pre-existing substrate filter."""
    clauses = ["workspace_id = ?"]
    params: list[str | int] = [workspace_id]
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(status.value for status in statuses)
    if action_statuses:
        placeholders = ",".join("?" for _ in action_statuses)
        clauses.append(f"action_status IN ({placeholders})")
        params.extend(s.value for s in action_statuses)
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
