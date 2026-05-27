"""Resolve the canonical candidates table name."""

from __future__ import annotations

import sqlite3


def resolve_candidates_table(conn: sqlite3.Connection) -> str | None:
    """Return ``candidates`` when the canonical table exists.

    Callers that get ``None`` should treat the write as a no-op and
    surface the missing-schema state to the operator.
    """
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='candidates'",
        ).fetchone()
    except sqlite3.Error:
        return None
    return "candidates" if row else None
