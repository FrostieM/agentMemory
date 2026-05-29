from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migration_validation import (
    _CANONICAL_SENTINEL_TABLES,
    _LEGACY_V2_TABLES,
)
from agent_memory_lite.db.migrations import (
    MIGRATION_DIR,
    apply_migrations,
    discover_migrations,
)
from agent_memory_lite.db.migrations import (
    main as migration_main,
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
        assert applied == [
            "0001_init",
            "0002_issues",
            "0003_code_digests_indexed_at",
            "0004_skills_base_confidence",
        ]
        versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert [row[0] for row in versions] == [
            "0001_init",
            "0002_issues",
            "0003_code_digests_indexed_at",
            "0004_skills_base_confidence",
        ]
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
            "tasks",
            "behaviors",
            "skills",
            "concepts",
            "code_digests",
            "files",
            "entities",
            "facts",
            "audit_log",
            "workspace_meta",
            "schema_migrations",
            "chunks_fts",
        ):
            assert required in tables, f"missing table: {required}"
        legacy_tables = {
            "active_edits",
            "agent_playbooks",
            "agent_roles",
            "agent_skills",
            "behavior_instructions",
            "core_memory",
            "domain_concepts",
            "procedural_rules",
            "research_insights",
            "task_state",
        }
        assert tables.isdisjoint(legacy_tables)
    finally:
        close_connection(conn)


def test_project_migration_rejects_pre_squash_0001_marker() -> None:
    conn = open_connection(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            ("0001_init", "2026-01-01T00:00:00Z"),
        )
        with pytest.raises(RuntimeError, match="canonical v3 tables"):
            apply_migrations(conn, MIGRATION_DIR)
    finally:
        close_connection(conn)


def test_project_migration_rejects_malformed_applied_baseline() -> None:
    conn = open_connection(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            ("0001_init", "2026-01-01T00:00:00Z"),
        )
        for table in sorted(_CANONICAL_SENTINEL_TABLES):
            conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")

        with pytest.raises(RuntimeError, match=r"malformed canonical v3 tables.*behaviors"):
            apply_migrations(conn, MIGRATION_DIR)
    finally:
        close_connection(conn)


def test_project_migration_rejects_legacy_tables_before_0001() -> None:
    conn = open_connection(":memory:")
    try:
        conn.execute("CREATE TABLE core_memory (id TEXT PRIMARY KEY)")
        with pytest.raises(RuntimeError, match="legacy tables"):
            apply_migrations(conn, MIGRATION_DIR)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        assert "core_memory" in tables
        assert "behaviors" not in tables
    finally:
        close_connection(conn)


@pytest.mark.parametrize("legacy_table", sorted(_LEGACY_V2_TABLES))
def test_project_migration_rejects_legacy_tables_in_applied_baseline(
    legacy_table: str,
) -> None:
    conn = open_connection(":memory:")
    try:
        apply_migrations(conn, MIGRATION_DIR)
        conn.execute(f"CREATE TABLE {legacy_table} (id TEXT PRIMARY KEY)")

        with pytest.raises(RuntimeError, match=legacy_table):
            apply_migrations(conn, MIGRATION_DIR)
    finally:
        close_connection(conn)


def test_migration_module_cli_applies_root_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    rc = migration_main(["--db", str(db_path)])
    assert rc == 0

    conn = open_connection(db_path)
    try:
        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()
        assert sorted(row[0] for row in versions) == [
            "0001_init",
            "0002_issues",
            "0003_code_digests_indexed_at",
            "0004_skills_base_confidence",
        ]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        assert "behaviors" in tables
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
