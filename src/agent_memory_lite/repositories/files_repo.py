"""SQL operations for the `files` table."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.files import FileRecord


def _row_to_file(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        path=row["path"],
        language=row["language"],
        content_hash=row["content_hash"],
        size_bytes=int(row["size_bytes"]),
        last_indexed_at=row["last_indexed_at"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def upsert_file_row(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    workspace_id: str,
    path: str,
    language: str | None,
    content_hash: str,
    size_bytes: int,
    metadata: dict[str, object],
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO files (
            id, workspace_id, path, language, content_hash,
            size_bytes, last_indexed_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, path) DO UPDATE SET
            language = excluded.language,
            content_hash = excluded.content_hash,
            size_bytes = excluded.size_bytes,
            last_indexed_at = excluded.last_indexed_at,
            metadata_json = excluded.metadata_json
        """,
        (
            file_id,
            workspace_id,
            path,
            language,
            content_hash,
            size_bytes,
            timestamp,
            json.dumps(metadata, sort_keys=True),
        ),
    )


def get_file_by_path(
    conn: sqlite3.Connection, *, workspace_id: str, path: str
) -> FileRecord | None:
    row = conn.execute(
        "SELECT * FROM files WHERE workspace_id = ? AND path = ?",
        (workspace_id, path),
    ).fetchone()
    return _row_to_file(row) if row is not None else None


def list_files(conn: sqlite3.Connection, workspace_id: str) -> list[FileRecord]:
    rows = conn.execute(
        "SELECT * FROM files WHERE workspace_id = ? ORDER BY path",
        (workspace_id,),
    ).fetchall()
    return [_row_to_file(r) for r in rows]


def delete_file(conn: sqlite3.Connection, file_id: str) -> int:
    cur = conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    return int(cur.rowcount)
