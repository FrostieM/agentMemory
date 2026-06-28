"""Batch A (M6/L4): gather_degradation builds the fast-path degradation summary.

Pins the degraded-flag semantics the three status surfaces rely on -- WARNING
housekeeping does NOT flip ``degraded``, ERROR/CRITICAL do, and the degradation
is ordered on top.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.api.routes.memory_status_queries import gather_degradation
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.ingestion.maintenance_writer import write_maintenance_event
from agent_memory_lite.models.enums import MaintenanceSeverity
from agent_memory_lite.models.maintenance import MaintenanceEventIn


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _event(
    conn: sqlite3.Connection, kind: str, severity: MaintenanceSeverity, fp: str, ws: str = "ws"
) -> None:
    write_maintenance_event(
        conn,
        MaintenanceEventIn(
            workspace_id=ws, kind=kind, severity=severity, summary=kind, details={"fingerprint": fp}
        ),
    )
    conn.commit()


def test_empty_is_not_degraded(conn: sqlite3.Connection) -> None:
    d = gather_degradation(conn, "ws")
    assert d.open_maintenance_events == 0
    assert d.degraded is False
    assert d.recent_degradations == []


def test_warning_only_is_not_degraded(conn: sqlite3.Connection) -> None:
    _event(conn, "drift_detected", MaintenanceSeverity.WARNING, "w1")
    d = gather_degradation(conn, "ws")
    assert d.open_maintenance_events == 1
    assert d.degraded is False  # WARNING housekeeping must not flip degraded


def test_error_is_degraded_and_ordered_first(conn: sqlite3.Connection) -> None:
    _event(conn, "drift_detected", MaintenanceSeverity.WARNING, "w1")
    _event(conn, "vector_upsert_failed", MaintenanceSeverity.ERROR, "e1")
    d = gather_degradation(conn, "ws")
    assert d.open_maintenance_events == 2
    assert d.degraded is True
    assert d.recent_degradations[0].severity == "error"  # real degradation on top


def test_other_workspace_events_excluded(conn: sqlite3.Connection) -> None:
    _event(conn, "vector_upsert_failed", MaintenanceSeverity.ERROR, "e1", ws="ws-other")
    d = gather_degradation(conn, "ws")
    assert d.open_maintenance_events == 0
    assert d.degraded is False
