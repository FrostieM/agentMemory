from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import (
    MIGRATION_DIR,
    apply_migrations,
    discover_migrations,
)


def _make_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    for name, sql in files.items():
        (d / name).write_text(sql, encoding="utf-8")
    return d


def test_discover_sorts_lexically(tmp_path: Path) -> None:
    d = _make_dir(
        tmp_path,
        {
            "0002_b.sql": "SELECT 1;",
            "0001_a.sql": "SELECT 1;",
            "0010_z.sql": "SELECT 1;",
        },
    )
    versions = [m.version for m in discover_migrations(d)]
    assert versions == ["0001_a", "0002_b", "0010_z"]


def test_discover_rejects_bad_filename(tmp_path: Path) -> None:
    d = _make_dir(tmp_path, {"bad.sql": "SELECT 1;"})
    with pytest.raises(ValueError, match="NNNN"):
        discover_migrations(d)


def test_apply_creates_tracking_table_and_runs(tmp_path: Path) -> None:
    d = _make_dir(tmp_path, {"0001_init.sql": "CREATE TABLE t (x INTEGER);"})
    conn = open_connection(":memory:")
    try:
        applied = apply_migrations(conn, d)
        assert applied == ["0001_init"]
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert [r[0] for r in rows] == ["0001_init"]
        conn.execute("INSERT INTO t VALUES (1)")
    finally:
        close_connection(conn)


def test_apply_is_idempotent(tmp_path: Path) -> None:
    d = _make_dir(
        tmp_path,
        {
            "0001_init.sql": "CREATE TABLE IF NOT EXISTS t (x INTEGER);",
        },
    )
    conn = open_connection(":memory:")
    try:
        first = apply_migrations(conn, d)
        second = apply_migrations(conn, d)
        assert first == ["0001_init"]
        assert second == []
    finally:
        close_connection(conn)


def test_real_project_migrations_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    conn = open_connection(db_path)
    try:
        applied = apply_migrations(conn, MIGRATION_DIR)
        assert "0001_init" in applied
        assert "0002_chunks_fts" in applied
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        for required in (
            "episodes",
            "chunks",
            "decisions",
            "task_state",
            "core_memory",
            "procedural_rules",
            "files",
            "entities",
            "facts",
            "audit_log",
            "workspace_meta",
            "schema_migrations",
            "chunks_fts",
        ):
            assert required in tables, f"missing table: {required}"
    finally:
        close_connection(conn)


def test_apply_skips_known_versions(tmp_path: Path) -> None:
    d = _make_dir(
        tmp_path,
        {
            "0001_a.sql": "CREATE TABLE IF NOT EXISTS a (x INTEGER);",
            "0002_b.sql": "CREATE TABLE IF NOT EXISTS b (x INTEGER);",
        },
    )
    conn: sqlite3.Connection = open_connection(":memory:")
    try:
        apply_migrations(conn, d)
        # Pretend 0002 wasn't applied; runner should re-apply it (no-op via IF NOT EXISTS).
        conn.execute("DELETE FROM schema_migrations WHERE version = '0002_b'")
        again = apply_migrations(conn, d)
        assert again == ["0002_b"]
    finally:
        close_connection(conn)
