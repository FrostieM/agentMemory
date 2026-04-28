"""Writers for memory maintenance events."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import MaintenanceEventStatus
from agent_memory_lite.models.maintenance import MaintenanceEvent, MaintenanceEventIn
from agent_memory_lite.repositories.maintenance_repo import (
    insert_maintenance_event_row,
    list_open_maintenance_events,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_maintenance_event(
    conn: sqlite3.Connection,
    payload: MaintenanceEventIn,
) -> MaintenanceEvent:
    event_id = new_id(IdKind.MAINTENANCE_EVENT)
    timestamp = iso_now()
    insert_maintenance_event_row(
        conn,
        event_id=event_id,
        workspace_id=payload.workspace_id,
        kind=payload.kind,
        severity=payload.severity,
        status=payload.status,
        summary=payload.summary,
        details=payload.details,
        source_episode_id=payload.source_episode_id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        created_at=timestamp,
    )
    # Return the inserted row through the normal model path.
    events = list_open_maintenance_events(conn, workspace_id=payload.workspace_id, limit=100)
    for event in events:
        if event.id == event_id:
            return event
    return MaintenanceEvent(
        id=event_id,
        workspace_id=payload.workspace_id,
        kind=payload.kind,
        severity=payload.severity,
        status=MaintenanceEventStatus.OPEN,
        summary=payload.summary,
        details=payload.details,
        source_episode_id=payload.source_episode_id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        created_at=timestamp,
        resolved_at=None,
    )
