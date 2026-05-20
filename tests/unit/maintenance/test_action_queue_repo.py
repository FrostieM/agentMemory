"""v3.4 #6 hygiene action queue — repository tests.

Covers the operator-side triage lifecycle on maintenance_events:

* claim_maintenance_event flips action_status='open' -> 'claimed' and
  stamps assigned_to + claimed_at without touching the substrate
  ``status`` column (the underlying drift is still real).
* dismiss_maintenance_event flips to 'dismissed' + dismissed_at.
* resolve_maintenance_event drives action_status to 'resolved' alongside
  the existing substrate ``status`` change.
* list_maintenance_events filters by ``action_statuses`` list as well as
  the pre-existing substrate ``statuses`` filter.
* row_to_event is migration-resilient: pre-migration rows (no
  action_status column) read back as MaintenanceActionStatus.OPEN.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.models.enums import (
    MaintenanceActionStatus,
    MaintenanceEventStatus,
    MaintenanceSeverity,
)
from agent_memory_lite.models.maintenance import MaintenanceEventIn
from agent_memory_lite.repositories.maintenance_repo import (
    claim_maintenance_event,
    dismiss_maintenance_event,
    list_maintenance_events,
    resolve_maintenance_event,
)


def _seed(conn: sqlite3.Connection, *, summary: str = "drift") -> str:
    event = write_maintenance_event(
        conn,
        MaintenanceEventIn(
            workspace_id="ws",
            kind="memory_drift_fk",
            severity=MaintenanceSeverity.WARNING,
            status=MaintenanceEventStatus.OPEN,
            summary=summary,
            details={"violations": 3},
        ),
    )
    return event.id


def test_claim_flips_action_status_only(applied_conn: sqlite3.Connection) -> None:
    """Claim updates triage state without touching substrate status."""
    event_id = _seed(applied_conn)
    result = claim_maintenance_event(
        applied_conn,
        event_id=event_id,
        assigned_to="osino",
        claimed_at="2026-05-20T18:00:00+00:00",
        action_notes="taking a look",
    )
    assert result is not None
    assert result.action_status == MaintenanceActionStatus.CLAIMED
    assert result.assigned_to == "osino"
    assert result.claimed_at == "2026-05-20T18:00:00+00:00"
    assert result.action_notes == "taking a look"
    # Substrate state is unchanged — the underlying drift is still real.
    assert result.status == MaintenanceEventStatus.OPEN
    assert result.resolved_at is None


def test_dismiss_flips_action_status_only(applied_conn: sqlite3.Connection) -> None:
    """Dismiss marks the ticket non-actionable; substrate status untouched."""
    event_id = _seed(applied_conn)
    result = dismiss_maintenance_event(
        applied_conn,
        event_id=event_id,
        dismissed_at="2026-05-20T18:01:00+00:00",
        action_notes="duplicate of #42",
    )
    assert result is not None
    assert result.action_status == MaintenanceActionStatus.DISMISSED
    assert result.dismissed_at == "2026-05-20T18:01:00+00:00"
    assert result.action_notes == "duplicate of #42"
    assert result.status == MaintenanceEventStatus.OPEN


def test_resolve_syncs_both_statuses(applied_conn: sqlite3.Connection) -> None:
    """resolve_maintenance_event is the substrate path → action_status
    follows to RESOLVED. Otherwise the queue would still show resolved
    events as 'open' for triage forever."""
    event_id = _seed(applied_conn)
    result = resolve_maintenance_event(
        applied_conn,
        event_id=event_id,
        status=MaintenanceEventStatus.RESOLVED,
        resolved_at="2026-05-20T18:02:00+00:00",
    )
    assert result is not None
    assert result.status == MaintenanceEventStatus.RESOLVED
    assert result.action_status == MaintenanceActionStatus.RESOLVED
    assert result.resolved_at == "2026-05-20T18:02:00+00:00"


def test_list_filter_by_action_status(applied_conn: sqlite3.Connection) -> None:
    """list_maintenance_events filters by action_statuses independently
    of substrate status."""
    open_id = _seed(applied_conn, summary="open one")
    claimed_id = _seed(applied_conn, summary="claimed one")
    claim_maintenance_event(
        applied_conn,
        event_id=claimed_id,
        assigned_to="osino",
        claimed_at="2026-05-20T18:00:00+00:00",
    )
    only_open = list_maintenance_events(
        applied_conn,
        workspace_id="ws",
        action_statuses=[MaintenanceActionStatus.OPEN],
    )
    assert [e.id for e in only_open] == [open_id]
    only_claimed = list_maintenance_events(
        applied_conn,
        workspace_id="ws",
        action_statuses=[MaintenanceActionStatus.CLAIMED],
    )
    assert [e.id for e in only_claimed] == [claimed_id]
    # Substrate status filter still works (both are still status='open').
    open_substrate = list_maintenance_events(
        applied_conn,
        workspace_id="ws",
        statuses=[MaintenanceEventStatus.OPEN],
    )
    assert {e.id for e in open_substrate} == {open_id, claimed_id}


def test_claim_returns_none_for_missing_event(applied_conn: sqlite3.Connection) -> None:
    """claim_maintenance_event returns None when the id doesn't exist —
    routes can map that to a 404 without re-querying first."""
    result = claim_maintenance_event(
        applied_conn,
        event_id="nope",
        assigned_to="osino",
        claimed_at="2026-05-20T18:00:00+00:00",
    )
    assert result is None


def test_row_to_event_defaults_action_status_when_column_missing() -> None:
    """The read path is resilient to a pre-migration row (no
    action_status column). This is the only path that runs against a
    DB created before migration 0036 — for example legacy test
    fixtures."""
    from agent_memory_lite.repositories.maintenance_queries import row_to_event  # noqa: PLC0415

    legacy = sqlite3.connect(":memory:")
    legacy.row_factory = sqlite3.Row
    legacy.executescript(
        """
        CREATE TABLE maintenance_events (
            id TEXT, workspace_id TEXT, kind TEXT, severity TEXT,
            status TEXT, summary TEXT, details_json TEXT,
            source_episode_id TEXT, target_type TEXT, target_id TEXT,
            created_at TEXT, resolved_at TEXT
        );
        INSERT INTO maintenance_events VALUES
            ('e1', 'ws', 'k', 'warning', 'open', 's', '{}',
             NULL, NULL, NULL, '2026-05-20T00:00:00+00:00', NULL);
        """
    )
    row = legacy.execute("SELECT * FROM maintenance_events").fetchone()
    event = row_to_event(row)
    assert event.action_status == MaintenanceActionStatus.OPEN
    assert event.assigned_to is None
    legacy.close()
