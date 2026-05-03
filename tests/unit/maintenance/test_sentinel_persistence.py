"""Sentinel result persistence + trend aggregation (v1.9)."""

from __future__ import annotations

import sqlite3
import time

from agent_memory_lite.maintenance.sentinel_persistence import (
    SentinelResultIn,
    record_sentinel_run,
    sentinel_trends,
)


def _result(*, case_name: str, status: str = "pass") -> SentinelResultIn:
    return SentinelResultIn(
        case_name=case_name,
        status=status,
        matched_count=3,
        expected_count=3,
        failures=[],
        metrics={"recall_at_k": 0.85},
    )


def _row_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM retrieval_sentinel_results").fetchone()[0])


def test_empty_results_returns_empty(applied_conn: sqlite3.Connection) -> None:
    run_id, ids = record_sentinel_run(applied_conn, workspace_id="default", results=[])
    assert run_id == ""
    assert ids == []
    assert _row_count(applied_conn) == 0


def test_single_run_writes_one_audit_row_per_batch(
    applied_conn: sqlite3.Connection,
) -> None:
    run_id, ids = record_sentinel_run(
        applied_conn,
        workspace_id="default",
        results=[
            _result(case_name="case_a"),
            _result(case_name="case_b", status="fail"),
        ],
    )
    assert run_id != ""
    assert len(ids) == 2
    assert _row_count(applied_conn) == 2
    audit_rows = applied_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'sentinel.run_recorded'"
    ).fetchone()
    assert int(audit_rows[0]) == 1


def test_run_id_ties_results_together(applied_conn: sqlite3.Connection) -> None:
    run_id, _ids = record_sentinel_run(
        applied_conn,
        workspace_id="default",
        results=[_result(case_name=f"case_{i}") for i in range(3)],
    )
    rows = applied_conn.execute("SELECT run_id FROM retrieval_sentinel_results").fetchall()
    assert {str(r[0]) for r in rows} == {run_id}


def test_sentinel_trends_aggregates_per_case(applied_conn: sqlite3.Connection) -> None:
    record_sentinel_run(
        applied_conn,
        workspace_id="default",
        results=[_result(case_name="case_a"), _result(case_name="case_b", status="fail")],
    )
    time.sleep(0.01)
    record_sentinel_run(
        applied_conn,
        workspace_id="default",
        results=[_result(case_name="case_a"), _result(case_name="case_b")],
    )
    trends = sentinel_trends(applied_conn, workspace_id="default", window_days=30)
    by_name = {t.case_name: t for t in trends}
    assert by_name["case_a"].runs == 2
    assert by_name["case_a"].passes == 2
    assert by_name["case_a"].last_status == "pass"
    assert by_name["case_b"].runs == 2
    assert by_name["case_b"].passes == 1
    assert by_name["case_b"].failures == 1
    assert by_name["case_b"].last_status == "pass"


def test_sentinel_trends_window_excludes_old(applied_conn: sqlite3.Connection) -> None:
    # Insert manually with old timestamp to simulate ageing.
    applied_conn.execute(
        """
        INSERT INTO retrieval_sentinel_results
        (id, workspace_id, case_name, status, matched_count, expected_count,
         failures_json, metrics_json, run_id, created_at)
        VALUES ('sent_old', 'default', 'case_a', 'pass', 3, 3, '[]', '{}',
                'old_run', '2020-01-01T00:00:00Z')
        """
    )
    trends = sentinel_trends(applied_conn, workspace_id="default", window_days=7)
    assert trends == []
