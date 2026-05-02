"""SQLite connection factory.

Opens a connection with a `Row` factory, applies the standard pragmas, and ensures
the parent directory exists. Callers close their own connections via `close_connection`
or the `closing` context manager.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from agent_memory_lite.db.pragmas import apply_pragmas


def open_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
        isolation_level=None,  # autocommit; explicit transactions via with_tx
    )
    conn.row_factory = sqlite3.Row
    apply_pragmas(conn)
    return conn


def close_connection(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):
        conn.close()
