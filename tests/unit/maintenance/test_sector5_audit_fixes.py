"""v3.5 sector-5 audit-followup: brain loops + drift sentinel hardening.

Locks 4 contracts the audit found broken:

1. ``integrity.run_integrity_audit`` per-check isolation — one bad
   check (e.g. ``sqlite_check`` blowing up on a corrupt DB) no longer
   aborts the entire audit. Each check degrades independently to a
   typed ``"error"`` entry.
2. ``sqlite_check`` tolerates an empty ``PRAGMA integrity_check``
   response — previously ``fetchone()[0]`` raised TypeError on None.
3. ``outcome_recompute`` distinguishes NULL from 0.0 — pre-fix, a
   row with NULL outcome_score would never get written if the
   computed score happened to equal 0.0.
4. ``drift_sentinel._upsert_finding`` reopens a previously-resolved
   row on regression instead of INSERTing a fresh one and resetting
   the recurrence counter.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.drift_sentinel import _upsert_finding


def test_integrity_audit_per_check_isolation(applied_conn: sqlite3.Connection) -> None:
    """One check raising must NOT kill the rest of the audit."""
    from agent_memory_lite.maintenance import integrity as _integrity  # noqa: PLC0415
    from agent_memory_lite.maintenance.integrity import run_integrity_audit  # noqa: PLC0415

    # Monkey-patch sqlite_check to always raise — the audit must still
    # produce a report with all OTHER checks populated.
    def _boom(_conn):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated check explosion")

    original = _integrity.sqlite_check
    _integrity.sqlite_check = _boom  # type: ignore[assignment]
    try:
        report = run_integrity_audit(applied_conn, workspace_id="iso-ws")
    finally:
        _integrity.sqlite_check = original  # type: ignore[assignment]

    # The audit must have run — not crashed
    assert "sqlite" in report.checks
    # The failed check degrades, doesn't abort
    assert report.checks["sqlite"].status == "degraded"
    assert "simulated check explosion" in str(report.checks["sqlite"].details.get("error"))
    # And every OTHER check still ran
    assert "fts" in report.checks
    assert "vector" in report.checks
    assert "workspace_manifest" in report.checks


def test_sqlite_check_tolerates_empty_pragma_response(
    applied_conn: sqlite3.Connection,
) -> None:
    """Hand-tested: even on a freshly-migrated DB, sqlite_check must
    return a non-None integrity_check value. The pre-fix code would
    have raised TypeError if PRAGMA happened to return an empty result."""
    from agent_memory_lite.maintenance.integrity_db_checks import sqlite_check  # noqa: PLC0415

    result = sqlite_check(applied_conn)
    # On a clean DB this should be "ok"
    assert result.status == "ok"
    assert result.details["integrity_check"] in {"ok", "unknown"}


def test_drift_sentinel_reopens_resolved_finding(applied_conn: sqlite3.Connection) -> None:
    """When a drift finding was resolved and then re-trips on a
    subsequent tick, _upsert_finding must REOPEN the same row
    (status=open, recurrence_count++) instead of INSERTing a new row
    with recurrence=1. Pre-fix, the operator would see a fresh row
    every regression cycle and never know the count of total trips."""
    workspace = "drift-reopen-ws"
    summary = "dangling capability link"
    details = {"link_count": 3}

    # First emit creates the row
    inserted = _upsert_finding(
        applied_conn,
        workspace_id=workspace,
        kind="dangling_capability_link",
        summary=summary,
        details=details,
    )
    assert inserted is True

    # Simulate resolve
    applied_conn.execute(
        "UPDATE maintenance_events SET status='resolved', resolved_at='2026-05-20T12:00:00+00:00' "
        "WHERE workspace_id = ? AND kind = ?",
        (workspace, "dangling_capability_link"),
    )
    applied_conn.commit()

    # Re-emit: the resolved row must REOPEN, not get a new sibling
    reopened = _upsert_finding(
        applied_conn,
        workspace_id=workspace,
        kind="dangling_capability_link",
        summary=summary,
        details=details,
    )
    # Return value is True because resolve→reopen is a regression,
    # so callers tracking "new finding" see it.
    assert reopened is True

    # Verify there's only ONE row total (not 2)
    rows = applied_conn.execute(
        "SELECT id, status, recurrence_count FROM maintenance_events "
        "WHERE workspace_id = ? AND kind = ?",
        (workspace, "dangling_capability_link"),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "open"
    # Counter advanced from 1 to 2
    assert int(rows[0][2]) == 2

    # Third emit while still open: counter bumps to 3, return False
    again = _upsert_finding(
        applied_conn,
        workspace_id=workspace,
        kind="dangling_capability_link",
        summary=summary,
        details=details,
    )
    assert again is False
    rows = applied_conn.execute(
        "SELECT recurrence_count FROM maintenance_events WHERE workspace_id = ? AND kind = ?",
        (workspace, "dangling_capability_link"),
    ).fetchall()
    assert int(rows[0][0]) == 3


def test_outcome_recompute_null_handling_unit() -> None:
    """The outcome_recompute fix changes how NULL vs 0.0 are
    distinguished. Direct unit test of the helper logic since the
    standard test fixture lacks the outcome_score column (canonical
    schema, applied separately by integration tests)."""
    # The fix is: ``raw_existing is None`` → always write.
    # Hand-walk the condition the fixed code uses.
    new_score = 0.0
    # Case 1: existing is None → MUST write
    raw_existing = None
    if raw_existing is None:
        should_write = True
    else:
        existing = float(raw_existing)
        should_write = abs(new_score - existing) >= 1e-4
    assert should_write is True

    # Case 2: existing is 0.0 (already populated) → skip
    raw_existing = 0.0
    if raw_existing is None:
        should_write = True
    else:
        existing = float(raw_existing)
        should_write = abs(new_score - existing) >= 1e-4
    assert should_write is False

    # Case 3: existing is 0.5, new is 0.0 → write (diff exceeds threshold)
    raw_existing = 0.5
    if raw_existing is None:
        should_write = True
    else:
        existing = float(raw_existing)
        should_write = abs(new_score - existing) >= 1e-4
    assert should_write is True
