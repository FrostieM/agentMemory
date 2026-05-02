"""Tests for the boot-time foreign-key drift guard."""

from __future__ import annotations

from pathlib import Path

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.integrity_check import (
    EVENT_KIND,
    record_foreign_key_violations,
)
from agent_memory_lite.db.migrations import apply_migrations
from agent_memory_lite.models.enums import MaintenanceEventStatus
from agent_memory_lite.repositories.maintenance_repo import list_maintenance_events


def _seed_dangling_chunk_episode(conn) -> None:
    """Insert a chunk that references an episode id that does not exist."""
    # Toggle FK enforcement off for this raw insert so SQLite lets us
    # write a dangling reference (mirrors the bulk-cleanup bug we are
    # guarding against).
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """
        INSERT INTO chunks (
            id, workspace_id, file_id, episode_id, kind, text, summary,
            line_start, line_end, symbols_json, embedding_id, importance,
            confidence, created_at, metadata_json, label
        ) VALUES (?, ?, NULL, ?, 'episode', 'orphan', NULL, NULL, NULL,
                  '[]', NULL, 0.5, 1.0, '2026-05-02T00:00:00+00:00', '{}', NULL)
        """,
        ("chk_orphan_test", "default", "ep_does_not_exist"),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def test_record_foreign_key_violations_is_noop_on_clean_db(tmp_path: Path) -> None:
    conn = open_connection(tmp_path / "memory.db")
    try:
        apply_migrations(conn)
        report = record_foreign_key_violations(conn, workspace_id="default")
        assert report["violations"] == 0
        assert report["event_id"] is None
        events = list_maintenance_events(
            conn, workspace_id="default", statuses=[MaintenanceEventStatus.OPEN], limit=10
        )
        assert all(e.kind != EVENT_KIND for e in events)
    finally:
        close_connection(conn)


def test_record_foreign_key_violations_writes_event_on_drift(tmp_path: Path) -> None:
    conn = open_connection(tmp_path / "memory.db")
    try:
        apply_migrations(conn)
        _seed_dangling_chunk_episode(conn)
        report = record_foreign_key_violations(conn, workspace_id="default")
        assert report["violations"] >= 1
        assert report["event_id"] is not None
        assert "chunks->episodes" in report["by_table"]
        events = list_maintenance_events(
            conn, workspace_id="default", statuses=[MaintenanceEventStatus.OPEN], limit=10
        )
        assert any(e.kind == EVENT_KIND for e in events)
    finally:
        close_connection(conn)


def test_record_foreign_key_violations_does_not_duplicate_events(tmp_path: Path) -> None:
    conn = open_connection(tmp_path / "memory.db")
    try:
        apply_migrations(conn)
        _seed_dangling_chunk_episode(conn)
        first = record_foreign_key_violations(conn, workspace_id="default")
        second = record_foreign_key_violations(conn, workspace_id="default")
        assert first["event_id"] is not None
        # The second call must NOT create another open event for the
        # same kind (the operator hasn't acknowledged the first yet).
        assert second["event_id"] is None
        events = [
            e
            for e in list_maintenance_events(
                conn,
                workspace_id="default",
                statuses=[MaintenanceEventStatus.OPEN],
                limit=10,
            )
            if e.kind == EVENT_KIND
        ]
        assert len(events) == 1
    finally:
        close_connection(conn)
