from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.db.transactions import with_tx


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE TABLE t (x INTEGER)")
    try:
        yield conn
    finally:
        conn.close()


def test_commits_on_clean_exit(conn: sqlite3.Connection) -> None:
    with with_tx(conn):
        conn.execute("INSERT INTO t VALUES (1)")
    rows = conn.execute("SELECT x FROM t").fetchall()
    assert rows == [(1,)]


def _raise_inside_tx(conn: sqlite3.Connection) -> None:
    with with_tx(conn):
        conn.execute("INSERT INTO t VALUES (1)")
        raise RuntimeError("boom")


def test_rolls_back_on_exception(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        _raise_inside_tx(conn)
    rows = conn.execute("SELECT x FROM t").fetchall()
    assert rows == []
