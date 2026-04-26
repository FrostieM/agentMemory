"""Property test: applying migrations from any starting state converges."""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from agent_memory_lite.db.connection import close_connection, open_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations


@given(applied_already=st.sets(st.sampled_from(["0001_init", "0002_chunks_fts"])))
@settings(max_examples=10, deadline=None)
def test_applies_only_missing_versions(tmp_path_factory, applied_already: set[str]) -> None:
    db_dir = tmp_path_factory.mktemp("mig_idemp")
    db_path = Path(db_dir) / "memory.db"
    conn = open_connection(db_path)
    try:
        apply_migrations(conn, MIGRATION_DIR)  # full apply first
        # Pretend a subset of versions wasn't applied yet.
        for version in applied_already:
            conn.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))
        replay = apply_migrations(conn, MIGRATION_DIR)
        assert set(replay) == applied_already
        # Subsequent run is a no-op.
        again = apply_migrations(conn, MIGRATION_DIR)
        assert again == []
    finally:
        close_connection(conn)
