"""Per-connection PRAGMA settings.

Setting these in code (rather than in a migration) ensures every connection — including
test connections to `:memory:` — gets the same behaviour. `journal_mode = WAL` is
persisted by SQLite, but re-asserting it is harmless.
"""

from __future__ import annotations

import sqlite3

PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("foreign_keys", "ON"),
    ("temp_store", "MEMORY"),
    ("busy_timeout", "5000"),
)


def apply_pragmas(conn: sqlite3.Connection) -> None:
    for pragma, value in PRAGMAS:
        if pragma == "journal_mode":
            conn.execute(f"PRAGMA {pragma} = {value}").fetchall()
        else:
            conn.execute(f"PRAGMA {pragma} = {value}")
