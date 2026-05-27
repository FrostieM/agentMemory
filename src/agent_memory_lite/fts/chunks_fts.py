"""Application-managed FTS5 sync for the `chunks_fts` virtual table.

Triggers are intentionally not installed; the FTS table is created by the squashed v3 schema in migrations/0001_init.sql.
Every chunk insert/delete in the service must go through these helpers.
"""

from __future__ import annotations

import json
import sqlite3


def _format_symbols(symbols: list[str] | str) -> str:
    if isinstance(symbols, str):
        try:
            data = json.loads(symbols)
        except json.JSONDecodeError:
            return ""
    else:
        data = symbols
    if not isinstance(data, list):
        return ""
    return " ".join(str(item) for item in data)


def insert_chunk_fts(
    conn: sqlite3.Connection,
    *,
    chunk_id: str,
    workspace_id: str,
    path: str | None,
    symbols: list[str] | str,
    text: str,
    summary: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO chunks_fts (chunk_id, workspace_id, path, symbols, text, summary)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            chunk_id,
            workspace_id,
            path or "",
            _format_symbols(symbols),
            text,
            summary or "",
        ),
    )


def delete_chunk_fts(conn: sqlite3.Connection, chunk_id: str) -> int:
    cur = conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
    return int(cur.rowcount)


def rebuild_chunks_fts(conn: sqlite3.Connection, workspace_id: str | None = None) -> int:
    """Drop and re-populate FTS rows for `workspace_id` (or every workspace)."""
    if workspace_id is None:
        conn.execute("DELETE FROM chunks_fts")
    else:
        conn.execute("DELETE FROM chunks_fts WHERE workspace_id = ?", (workspace_id,))

    rows = conn.execute(
        """
        SELECT c.id, c.workspace_id, f.path, c.symbols_json, c.text, c.summary
        FROM chunks c
        LEFT JOIN files f ON f.id = c.file_id
        WHERE (? IS NULL OR c.workspace_id = ?)
        """,
        (workspace_id, workspace_id),
    ).fetchall()

    inserted = 0
    for row in rows:
        insert_chunk_fts(
            conn,
            chunk_id=row["id"],
            workspace_id=row["workspace_id"],
            path=row["path"],
            symbols=row["symbols_json"],
            text=row["text"],
            summary=row["summary"],
        )
        inserted += 1
    return inserted
