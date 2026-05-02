"""Writers for memory maintenance events."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.enums import MaintenanceEventStatus
from agent_memory_lite.models.maintenance import MaintenanceEvent, MaintenanceEventIn
from agent_memory_lite.repositories.maintenance_repo import (
    insert_maintenance_event_row,
    list_open_maintenance_events,
    update_maintenance_event_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_maintenance_event(
    conn: sqlite3.Connection,
    payload: MaintenanceEventIn,
) -> MaintenanceEvent:
    timestamp = iso_now()
    fingerprint = payload.details.get("fingerprint")
    if fingerprint:
        events = list_open_maintenance_events(conn, workspace_id=payload.workspace_id, limit=200)
        for event in events:
            if event.kind != payload.kind or event.details.get("fingerprint") != fingerprint:
                continue
            details = dict(event.details)
            details.update(payload.details)
            details.setdefault("first_seen_at", event.created_at)
            details["last_seen_at"] = timestamp
            details["seen_count"] = int(event.details.get("seen_count", 1) or 1) + 1
            updated = update_maintenance_event_row(
                conn,
                event_id=event.id,
                severity=payload.severity,
                status=payload.status,
                summary=payload.summary,
                details=details,
                target_type=payload.target_type,
                target_id=payload.target_id,
            )
            if updated is not None:
                return updated

    event_id = new_id(IdKind.MAINTENANCE_EVENT)
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
