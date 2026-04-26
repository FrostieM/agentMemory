"""Explicit transaction context manager.

Connections are opened in autocommit mode (`isolation_level=None`) so callers
must use `with_tx(conn)` to scope a transaction. This makes the boundary obvious
in code and keeps tests deterministic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def with_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
