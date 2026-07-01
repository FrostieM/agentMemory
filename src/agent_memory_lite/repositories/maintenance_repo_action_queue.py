"""Operator action-queue writers for maintenance events.

Split out of ``maintenance_repo.py`` so the repo file stays under the
SLOC budget. These are the operator-facing transitions that touch only
``action_status`` (claim / dismiss) without changing the substrate
``status`` -- the underlying drift may still be real. The substrate
resolve path lives in ``maintenance_repo.py``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import MaintenanceActionStatus
from agent_memory_lite.models.maintenance import MaintenanceEvent
from agent_memory_lite.repositories.maintenance_queries import row_to_event


def claim_maintenance_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    workspace_id: str,
    assigned_to: str,
    claimed_at: str,
    action_notes: str | None = None,
) -> MaintenanceEvent | None:
    """v3.4 #6 — operator claims an event for triage. Does NOT touch
    substrate ``status`` (the underlying drift is still real). Idempotent
    only in the sense that re-claiming bumps ``claimed_at`` and may
    re-assign; nothing prevents one operator overwriting another's claim
    because the queue UI shows the assignee in the row chip. round-D: the
    UPDATE is scoped to workspace_id so a shared-DB hub cannot claim a foreign
    workspace's event by id."""
    cur = conn.execute(
        """
        UPDATE maintenance_events
        SET action_status = ?,
            assigned_to = ?,
            claimed_at = ?,
            action_notes = COALESCE(?, action_notes)
        WHERE id = ? AND workspace_id = ?
        """,
        (
            MaintenanceActionStatus.CLAIMED.value,
            assigned_to,
            claimed_at,
            action_notes,
            event_id,
            workspace_id,
        ),
    )
    if cur.rowcount == 0:
        return None
    row = conn.execute(
        "SELECT * FROM maintenance_events WHERE id = ? AND workspace_id = ?",
        (event_id, workspace_id),
    ).fetchone()
    return row_to_event(row) if row is not None else None


def dismiss_maintenance_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    workspace_id: str,
    dismissed_at: str,
    action_notes: str | None = None,
) -> MaintenanceEvent | None:
    """v3.4 #6 — operator marks the finding non-actionable. Distinct from
    the substrate IGNORED ``status``: the underlying drift may still be
    real, the operator just decided this particular ticket is not worth
    chasing (false positive, duplicate of another open ticket, expected
    transient, etc.). ``status`` stays as-is. round-D: scoped to workspace_id."""
    cur = conn.execute(
        """
        UPDATE maintenance_events
        SET action_status = ?,
            dismissed_at = ?,
            action_notes = COALESCE(?, action_notes)
        WHERE id = ? AND workspace_id = ?
        """,
        (
            MaintenanceActionStatus.DISMISSED.value,
            dismissed_at,
            action_notes,
            event_id,
            workspace_id,
        ),
    )
    if cur.rowcount == 0:
        return None
    row = conn.execute(
        "SELECT * FROM maintenance_events WHERE id = ? AND workspace_id = ?",
        (event_id, workspace_id),
    ).fetchone()
    return row_to_event(row) if row is not None else None
