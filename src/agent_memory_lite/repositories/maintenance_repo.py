"""SQL writers for maintenance events.

Read-only queries (count / list) live in ``maintenance_queries.py``.
This module owns INSERT / UPDATE / resolve plus the row -> model
helper, kept here for backward-compatible imports.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEvent
from agent_memory_lite.repositories.maintenance_queries import (
    count_open_maintenance_events,
    list_maintenance_events,
    list_open_maintenance_events,
    row_to_event,
)

# Re-export read-side helpers so existing imports keep working without
# every call site moving to the new module right away.
__all__ = [
    "count_open_maintenance_events",
    "insert_maintenance_event_row",
    "list_maintenance_events",
    "list_open_maintenance_events",
    "resolve_maintenance_event",
    "update_maintenance_event_row",
]


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


def update_maintenance_event_row(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    severity: MaintenanceSeverity,
    status: MaintenanceEventStatus,
    summary: str,
    details: dict[str, Any],
    target_type: str | None,
    target_id: str | None,
) -> MaintenanceEvent | None:
    conn.execute(
        """
        UPDATE maintenance_events
        SET severity = ?,
            status = ?,
            summary = ?,
            details_json = ?,
            target_type = ?,
            target_id = ?
        WHERE id = ?
        """,
        (
            severity.value,
            status.value,
            summary,
            json.dumps(details, sort_keys=True, default=str),
            target_type,
            target_id,
            event_id,
        ),
    )
    row = conn.execute("SELECT * FROM maintenance_events WHERE id = ?", (event_id,)).fetchone()
    return row_to_event(row) if row is not None else None


def resolve_maintenance_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    status: MaintenanceEventStatus,
    resolved_at: str,
) -> MaintenanceEvent | None:
    if status == MaintenanceEventStatus.OPEN:
        raise ValueError("maintenance event resolution status must be resolved or ignored")
    conn.execute(
        """
        UPDATE maintenance_events
        SET status = ?, resolved_at = ?
        WHERE id = ?
        """,
        (status.value, resolved_at, event_id),
    )
    row = conn.execute("SELECT * FROM maintenance_events WHERE id = ?", (event_id,)).fetchone()
    return row_to_event(row) if row is not None else None
