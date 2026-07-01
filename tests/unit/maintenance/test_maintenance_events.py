from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.models.enums import MaintenanceEventStatus, MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEventIn
from agent_memory_lite.repositories.maintenance_repo import (
    list_open_maintenance_events,
    resolve_maintenance_event,
)
from agent_memory_lite.repositories.maintenance_repo_action_queue import (
    claim_maintenance_event,
    dismiss_maintenance_event,
)


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


def _seed_event(conn: sqlite3.Connection, workspace_id: str, target_id: str):
    return write_maintenance_event(
        conn,
        MaintenanceEventIn(
            workspace_id=workspace_id,
            kind="memory_watchdog",
            severity=MaintenanceSeverity.WARNING,
            status=MaintenanceEventStatus.OPEN,
            summary="w",
            details={},
            target_type="memory_watchdog",
            target_id=target_id,
        ),
    )


def test_maintenance_event_mutations_are_workspace_scoped(
    applied_conn: sqlite3.Connection,
) -> None:
    """round-D: resolve / claim / dismiss now filter by workspace_id, so a shared-DB
    hub cannot mutate another workspace's event by guessing its id -- a mismatched
    workspace_id is a no-op (returns None) and leaves the row untouched."""
    ev = _seed_event(applied_conn, "ws-a", "run-1")

    # Wrong workspace: every mutation is a no-op.
    assert (
        resolve_maintenance_event(
            applied_conn,
            event_id=ev.id,
            workspace_id="ws-b",
            status=MaintenanceEventStatus.RESOLVED,
            resolved_at="t",
        )
        is None
    )
    assert (
        claim_maintenance_event(
            applied_conn, event_id=ev.id, workspace_id="ws-b", assigned_to="x", claimed_at="t"
        )
        is None
    )
    assert (
        dismiss_maintenance_event(
            applied_conn, event_id=ev.id, workspace_id="ws-b", dismissed_at="t"
        )
        is None
    )
    # The owning workspace can still resolve it.
    resolved = resolve_maintenance_event(
        applied_conn,
        event_id=ev.id,
        workspace_id="ws-a",
        status=MaintenanceEventStatus.RESOLVED,
        resolved_at="t",
    )
    assert resolved is not None
    assert resolved.id == ev.id
