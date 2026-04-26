"""Workspace-scoped key/value metadata stored in the `workspace_meta` table.

Used to pin the embedding model and dimension for a workspace so we can detect drift
on startup (e.g. a different model would silently produce non-comparable vectors).
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.utils.time import iso_now


def get(conn: sqlite3.Connection, workspace_id: str, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM workspace_meta WHERE workspace_id = ? AND key = ?",
        (workspace_id, key),
    ).fetchone()
    return None if row is None else str(row[0])


def set_value(
    conn: sqlite3.Connection,
    workspace_id: str,
    key: str,
    value: str,
) -> None:
    conn.execute(
        """
        INSERT INTO workspace_meta (workspace_id, key, value, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(workspace_id, key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (workspace_id, key, value, iso_now()),
    )


def all_for(conn: sqlite3.Connection, workspace_id: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM workspace_meta WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}
