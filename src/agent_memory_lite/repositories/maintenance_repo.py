"""SQL writers for maintenance events.

Read-only queries (count / list) live in ``maintenance_queries.py``.
This module owns INSERT / UPDATE / resolve plus the row -> model
helper, kept here for backward-compatible imports.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.models.enums import (
    MaintenanceActionStatus,
    MaintenanceEventStatus,
    MaintenanceSeverity,
)
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
    "claim_maintenance_event",
    "count_open_maintenance_events",
    "dismiss_maintenance_event",
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
    # v3.4 #6: keep substrate ``status`` and operator ``action_status`` in
    # sync on resolve. resolve_maintenance_event is the substrate path
    # (metric cleared or operator marked done) so the operator state goes
    # to RESOLVED too. The dedicated claim / dismiss paths only touch
    # ``action_status`` because the substrate state may still be open.
    conn.execute(
        """
        UPDATE maintenance_events
        SET status = ?, resolved_at = ?,
            action_status = ?
        WHERE id = ?
        """,
        (status.value, resolved_at, MaintenanceActionStatus.RESOLVED.value, event_id),
    )
    row = conn.execute("SELECT * FROM maintenance_events WHERE id = ?", (event_id,)).fetchone()
    return row_to_event(row) if row is not None else None


def claim_maintenance_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    assigned_to: str,
    claimed_at: str,
    action_notes: str | None = None,
) -> MaintenanceEvent | None:
    """v3.4 #6 — operator claims an event for triage. Does NOT touch
    substrate ``status`` (the underlying drift is still real). Idempotent
    only in the sense that re-claiming bumps ``claimed_at`` and may
    re-assign; nothing prevents one operator overwriting another's claim
    because the queue UI shows the assignee in the row chip."""
    cur = conn.execute(
        """
        UPDATE maintenance_events
        SET action_status = ?,
            assigned_to = ?,
            claimed_at = ?,
            action_notes = COALESCE(?, action_notes)
        WHERE id = ?
        """,
        (
            MaintenanceActionStatus.CLAIMED.value,
            assigned_to,
            claimed_at,
            action_notes,
            event_id,
        ),
    )
    if cur.rowcount == 0:
        return None
    row = conn.execute("SELECT * FROM maintenance_events WHERE id = ?", (event_id,)).fetchone()
    return row_to_event(row) if row is not None else None


def dismiss_maintenance_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    dismissed_at: str,
    action_notes: str | None = None,
) -> MaintenanceEvent | None:
    """v3.4 #6 — operator marks the finding non-actionable. Distinct from
    the substrate IGNORED ``status``: the underlying drift may still be
    real, the operator just decided this particular ticket is not worth
    chasing (false positive, duplicate of another open ticket, expected
    transient, etc.). ``status`` stays as-is."""
    cur = conn.execute(
        """
        UPDATE maintenance_events
        SET action_status = ?,
            dismissed_at = ?,
            action_notes = COALESCE(?, action_notes)
        WHERE id = ?
        """,
        (
            MaintenanceActionStatus.DISMISSED.value,
            dismissed_at,
            action_notes,
            event_id,
        ),
    )
    if cur.rowcount == 0:
        return None
    row = conn.execute("SELECT * FROM maintenance_events WHERE id = ?", (event_id,)).fetchone()
    return row_to_event(row) if row is not None else None
