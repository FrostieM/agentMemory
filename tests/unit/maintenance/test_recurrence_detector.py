"""Recurrence detector: dedup-and-increment instead of duplicate inserts."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.recurrence_detector import upsert_finding_event


def _events_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM maintenance_events").fetchone()[0])


def _audit_count(conn: sqlite3.Connection, *, action: str) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = ?", (action,)).fetchone()[0]
    )


def test_first_finding_inserts_new_event(applied_conn: sqlite3.Connection) -> None:
    result = upsert_finding_event(
        applied_conn,
        workspace_id="default",
        kind="stale_candidate",
        severity="warning",
        summary="cand_a is old",
        details={},
        target_type="memory_candidate",
        target_id="cand_a",
        threshold=3,
    )
    assert result.is_new is True
    assert result.recurrence_count == 1
    assert _events_count(applied_conn) == 1


def test_repeat_finding_increments_existing_event(applied_conn: sqlite3.Connection) -> None:
    for _ in range(3):
        upsert_finding_event(
            applied_conn,
            workspace_id="default",
            kind="stale_candidate",
            severity="warning",
            summary="cand_a is old",
            details={},
            target_type="memory_candidate",
            target_id="cand_a",
            threshold=10,
        )
    assert _events_count(applied_conn) == 1
    row = applied_conn.execute("SELECT recurrence_count FROM maintenance_events").fetchone()
    assert int(row[0]) == 3


def test_threshold_crossing_emits_audit(applied_conn: sqlite3.Connection) -> None:
    for _ in range(2):
        upsert_finding_event(
            applied_conn,
            workspace_id="default",
            kind="stale_candidate",
            severity="warning",
            summary="cand_a is old",
            details={},
            target_type="memory_candidate",
            target_id="cand_a",
            threshold=3,
        )
    # No audit yet — recurrence_count is 2 < threshold 3.
    assert _audit_count(applied_conn, action="maintenance.recurrence_threshold_crossed") == 0
    result = upsert_finding_event(
        applied_conn,
        workspace_id="default",
        kind="stale_candidate",
        severity="warning",
        summary="cand_a is old",
        details={},
        target_type="memory_candidate",
        target_id="cand_a",
        threshold=3,
    )
    assert result.crossed_threshold is True
    assert _audit_count(applied_conn, action="maintenance.recurrence_threshold_crossed") == 1


def test_different_targets_create_separate_events(applied_conn: sqlite3.Connection) -> None:
    upsert_finding_event(
        applied_conn,
        workspace_id="default",
        kind="stale_candidate",
        severity="warning",
        summary="cand_a",
        details={},
        target_type="memory_candidate",
        target_id="cand_a",
        threshold=3,
    )
    upsert_finding_event(
        applied_conn,
        workspace_id="default",
        kind="stale_candidate",
        severity="warning",
        summary="cand_b",
        details={},
        target_type="memory_candidate",
        target_id="cand_b",
        threshold=3,
    )
    assert _events_count(applied_conn) == 2


def test_audit_emitted_only_once_at_first_crossing(applied_conn: sqlite3.Connection) -> None:
    for _ in range(5):
        upsert_finding_event(
            applied_conn,
            workspace_id="default",
            kind="stale_candidate",
            severity="warning",
            summary="cand_a",
            details={},
            target_type="memory_candidate",
            target_id="cand_a",
            threshold=3,
        )
    # 1 -> 2 -> 3 (cross!) -> 4 -> 5: only one audit row
    assert _audit_count(applied_conn, action="maintenance.recurrence_threshold_crossed") == 1
