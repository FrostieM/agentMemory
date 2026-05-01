from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEventIn
from agent_memory_lite.repositories.maintenance_repo import list_open_maintenance_events


def test_write_maintenance_event_deduplicates_open_fingerprint(
    applied_conn: sqlite3.Connection,
) -> None:
    first = write_maintenance_event(
        applied_conn,
        MaintenanceEventIn(
            workspace_id="project-a",
            kind="memory_watchdog",
            severity=MaintenanceSeverity.WARNING,
            status=MaintenanceEventStatus.OPEN,
            summary="Memory watchdog reported warning.",
            details={"fingerprint": "same-root-cause", "seen_count": 1},
            target_type="memory_watchdog",
            target_id="run-1",
        ),
    )
    second = write_maintenance_event(
        applied_conn,
        MaintenanceEventIn(
            workspace_id="project-a",
            kind="memory_watchdog",
            severity=MaintenanceSeverity.WARNING,
            status=MaintenanceEventStatus.OPEN,
            summary="Memory watchdog reported warning.",
            details={"fingerprint": "same-root-cause", "seen_count": 1},
            target_type="memory_watchdog",
            target_id="run-2",
        ),
    )

    assert second.id == first.id
    assert second.details["fingerprint"] == "same-root-cause"
    assert second.details["seen_count"] == 2
    assert second.details["last_seen_at"]
    assert len(list_open_maintenance_events(applied_conn, workspace_id="project-a")) == 1
