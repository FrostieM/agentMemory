"""Unit tests for repositories/audit_repo.py — insert_audit.

insert_audit's OperationalError fallback used to retry the INSERT
unconditionally. When the audit_log table is absent entirely (not just
the agent_id column), the retry threw again, uncaught, and 500'd
the operation being audited. The fallback is now best-effort — a missing
table is swallowed, the in-memory AuditEntry is still returned.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

import pytest

from agent_memory_lite.models.audit import AuditEntry
from agent_memory_lite.repositories.audit_repo import insert_audit


def test_insert_audit_writes_row(applied_conn: sqlite3.Connection) -> None:
    """Happy path: a migrated DB persists the audit row with agent_id."""
    entry = insert_audit(
        applied_conn,
        workspace_id="ws",
        action="write_decision",
        target_type="decision",
        target_id="dec_x",
        agent_id="tester",
    )
    assert isinstance(entry, AuditEntry)
    row = applied_conn.execute(
        "SELECT action, target_id, agent_id FROM audit_log WHERE id = ?", (entry.id,)
    ).fetchone()
    assert row is not None
    assert row["action"] == "write_decision"
    assert row["target_id"] == "dec_x"
    assert row["agent_id"] == "tester"


def test_insert_audit_missing_table_does_not_raise(
    fresh_conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """The exact crash this fix closes: a DB without the audit_log table
    must NOT crash the audited operation. insert_audit returns the
    in-memory AuditEntry best-effort instead of raising OperationalError,
    and logs the dropped row at WARNING so the gap is visible, not silent."""
    # fresh_conn has no migrations applied -> no audit_log table.
    with caplog.at_level(logging.WARNING):
        entry = insert_audit(
            fresh_conn,
            workspace_id="ws",
            action="search",
            target_type="query",
            target_id="q1",
        )
    assert isinstance(entry, AuditEntry)
    assert entry.action == "search"
    assert "audit_log write skipped" in caplog.text


def test_insert_audit_partial_schema_without_agent_id(fresh_conn: sqlite3.Connection) -> None:
    """audit_log exists but lacks agent_id; insert_audit still writes."""
    fresh_conn.execute(
        """
        CREATE TABLE audit_log (
            id TEXT PRIMARY KEY, workspace_id TEXT, action TEXT,
            target_type TEXT, target_id TEXT, source_episode_id TEXT,
            before_json TEXT, after_json TEXT, created_at TEXT
        )
        """
    )
    entry = insert_audit(
        fresh_conn,
        workspace_id="ws",
        action="archive",
        target_type="decision",
        target_id="dec_y",
        agent_id="tester",
    )
    row = fresh_conn.execute("SELECT action FROM audit_log WHERE id = ?", (entry.id,)).fetchone()
    assert row is not None
    assert row["action"] == "archive"


def test_insert_audit_non_serializable_payload_does_not_raise(
    applied_conn: sqlite3.Connection,
) -> None:
    """A payload value json cannot serialize natively (here a datetime) must
    not raise TypeError: insert_audit serializes with default=str, so the
    audit write completes instead of crashing the operation being audited."""
    entry = insert_audit(
        applied_conn,
        workspace_id="ws",
        action="write_decision",
        target_type="decision",
        target_id="dec_z",
        after={"committed_at": datetime(2026, 5, 21, 12, 0, 0)},
        agent_id="tester",
    )
    assert isinstance(entry, AuditEntry)
    row = applied_conn.execute(
        "SELECT after_json FROM audit_log WHERE id = ?", (entry.id,)
    ).fetchone()
    assert row is not None
    assert "2026-05-21" in row["after_json"]
