"""v3.2 — dead behavior auto-archive tests.

Locks the filter so a future regression can't accidentally archive
pinned, system-priority, recently-fired, or young behaviors.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_memory_lite.maintenance.behavior_auto_archive import (
    auto_archive_dead_behaviors,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Use apply_migrations to set up the live canonical schema."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(tmp_path / "test.db")
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_behavior(
    conn: sqlite3.Connection,
    *,
    bid: str,
    name: str,
    workspace_id: str = "ws",
    active: int = 1,
    application_count: int = 0,
    last_applied_at: str | None = None,
    pinned: int = 0,
    priority: str = "user_preference",
    age_days: int = 60,
) -> None:
    """Seed one canonical behavior row with the given knobs."""
    created = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, rule, rationale,
            priority, active, application_count, last_applied_at,
            pinned, created_at, updated_at)
           VALUES (?, ?, ?, 'workflow', 'do thing', '',
                   ?, ?, ?, ?, ?, ?, ?)""",
        (
            bid,
            workspace_id,
            name,
            priority,
            active,
            application_count,
            last_applied_at,
            pinned,
            created,
            created,
        ),
    )
    conn.commit()


def _is_active(conn: sqlite3.Connection, bid: str) -> bool:
    row = conn.execute("SELECT active FROM behaviors WHERE id = ?", (bid,)).fetchone()
    return bool(row and row["active"])


def test_dead_old_unpinned_behavior_is_archived(conn: sqlite3.Connection) -> None:
    """Baseline: 60-day-old never-fired non-pinned behavior → archived."""
    _seed_behavior(conn, bid="bi_dead", name="dead-rule")
    result = auto_archive_dead_behaviors(conn, workspace_id="ws", age_days=30)
    assert result.archived == 1
    assert result.scanned == 1
    assert not _is_active(conn, "bi_dead")


def test_pinned_behavior_never_archived(conn: sqlite3.Connection) -> None:
    """Operator-pinned rules are untouchable even if they never fired."""
    _seed_behavior(conn, bid="bi_pinned", name="pinned-rule", pinned=1)
    result = auto_archive_dead_behaviors(conn, workspace_id="ws", age_days=30)
    assert result.archived == 0
    assert _is_active(conn, "bi_pinned")


def test_system_priority_behavior_never_archived(conn: sqlite3.Connection) -> None:
    """priority='system' = infrastructure rule, do not touch."""
    _seed_behavior(conn, bid="bi_sys", name="system-rule", priority="system")
    result = auto_archive_dead_behaviors(conn, workspace_id="ws", age_days=30)
    assert result.archived == 0
    assert _is_active(conn, "bi_sys")


def test_recently_fired_behavior_never_archived(conn: sqlite3.Connection) -> None:
    """A behavior with application_count > 0 is in use — don't archive."""
    _seed_behavior(
        conn,
        bid="bi_used",
        name="used-rule",
        application_count=5,
        last_applied_at="2026-05-19T00:00:00+00:00",
    )
    result = auto_archive_dead_behaviors(conn, workspace_id="ws", age_days=30)
    assert result.archived == 0
    assert _is_active(conn, "bi_used")


def test_young_behavior_not_archived(conn: sqlite3.Connection) -> None:
    """Recently-created behaviors need observation time — give them 30 days."""
    _seed_behavior(conn, bid="bi_young", name="young-rule", age_days=5)
    result = auto_archive_dead_behaviors(conn, workspace_id="ws", age_days=30)
    assert result.archived == 0
    assert _is_active(conn, "bi_young")


def test_already_archived_behavior_skipped(conn: sqlite3.Connection) -> None:
    """active=0 rows are not re-touched (idempotent)."""
    _seed_behavior(conn, bid="bi_was_archived", name="prior-archive", active=0)
    result = auto_archive_dead_behaviors(conn, workspace_id="ws", age_days=30)
    assert result.archived == 0


def test_last_applied_at_set_blocks_archive(conn: sqlite3.Connection) -> None:
    """Even when application_count=0, a last_applied_at trace means the row
    fired at least once — don't archive (operator may have reset count)."""
    _seed_behavior(
        conn,
        bid="bi_traced",
        name="traced-rule",
        application_count=0,
        last_applied_at="2026-05-19T00:00:00+00:00",
    )
    result = auto_archive_dead_behaviors(conn, workspace_id="ws", age_days=30)
    assert result.archived == 0
    assert _is_active(conn, "bi_traced")


def test_workspace_isolation(conn: sqlite3.Connection) -> None:
    """Archive in workspace A must not affect workspace B."""
    _seed_behavior(conn, bid="bi_a", name="rule-in-a", workspace_id="ws_a")
    _seed_behavior(conn, bid="bi_b", name="rule-in-b", workspace_id="ws_b")
    result = auto_archive_dead_behaviors(conn, workspace_id="ws_a", age_days=30)
    assert result.archived == 1
    assert not _is_active(conn, "bi_a")
    assert _is_active(conn, "bi_b")


def test_archived_row_carries_reviewed_by_tag(conn: sqlite3.Connection) -> None:
    """Operator can grep for auto-archived rows via reviewed_by column."""
    _seed_behavior(conn, bid="bi_dead2", name="dead-rule-2")
    auto_archive_dead_behaviors(conn, workspace_id="ws", age_days=30)
    row = conn.execute(
        "SELECT reviewed_by, reviewed_at, expires_at FROM behaviors WHERE id = 'bi_dead2'"
    ).fetchone()
    assert row["reviewed_by"] == "auto_archive_v3_2"
    assert row["reviewed_at"] is not None
    assert row["expires_at"] is not None


def test_failure_soft_when_table_missing(tmp_path: Path) -> None:
    """Pre-migration DB (no behaviors table) returns zero
    instead of raising — brain_pass keeps moving."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        result = auto_archive_dead_behaviors(c, workspace_id="ws")
        assert result.archived == 0
        assert result.scanned == 0
    finally:
        c.close()
