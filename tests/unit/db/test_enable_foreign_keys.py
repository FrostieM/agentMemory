"""`enable_foreign_keys` turns on FK enforcement for raw background connections.

The maintenance write paths (sentinel/brain pass, hebbian distill, outcome
refresh, digest upsert) open their own ``sqlite3.connect`` and so default to
``foreign_keys=OFF`` -- a dangling reference would be written silently. These
tests lock the contract: the pragma flips, a dangling child is then rejected,
and (as a control) it is silently allowed without the call.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.db.pragmas import enable_foreign_keys


def _fk_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE child (id INTEGER PRIMARY KEY, "
        "parent_id INTEGER REFERENCES parent(id))"
    )


def test_sets_pragma_on() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0  # SQLite default
        enable_foreign_keys(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_enforced_after_enable_rejects_dangling_child() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        _fk_schema(conn)
        enable_foreign_keys(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")
    finally:
        conn.close()


def test_without_enable_dangling_child_is_silently_allowed() -> None:
    """Control: this is exactly the silent corruption the call closes -- with FK
    off (the default for a raw background connection) the dangling row sticks."""
    conn = sqlite3.connect(":memory:")
    try:
        _fk_schema(conn)
        conn.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")
        assert conn.execute("SELECT parent_id FROM child WHERE id = 1").fetchone()[0] == 999
    finally:
        conn.close()
