from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_lite.db.connection import close_connection, open_connection


def test_open_connection_creates_parent_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "deeper" / "memory.db"
    conn = open_connection(db_path)
    try:
        assert db_path.parent.exists()
        assert isinstance(conn, sqlite3.Connection)
    finally:
        close_connection(conn)


def test_open_connection_applies_pragmas(tmp_path: Path) -> None:
    conn = open_connection(tmp_path / "memory.db")
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal_mode).lower() == "wal"
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert int(foreign_keys) == 1
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert int(synchronous) == 1  # NORMAL
    finally:
        close_connection(conn)


def test_in_memory_connection_works() -> None:
    conn = open_connection(":memory:")
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        row = conn.execute("SELECT x FROM t").fetchone()
        assert row[0] == 1
    finally:
        close_connection(conn)


def test_close_connection_idempotent(tmp_path: Path) -> None:
    conn = open_connection(tmp_path / "memory.db")
    close_connection(conn)
    close_connection(conn)  # second close must not raise
