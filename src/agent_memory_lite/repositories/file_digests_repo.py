"""SQL operations for the ``file_digests`` table (1.8.0)."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.file_digests import FileDigest, FileDigestIn
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def _row_to_digest(row: sqlite3.Row) -> FileDigest:
    return FileDigest(
        id=row["id"],
        workspace_id=row["workspace_id"],
        file_path=row["file_path"],
        language=row["language"],
        chunk_count=int(row["chunk_count"]),
        symbol_count=int(row["symbol_count"]),
        inbound_edge_count=int(row["inbound_edge_count"]),
        outbound_edge_count=int(row["outbound_edge_count"]),
        versions_recent=int(row["versions_recent"]),
        narrative=row["narrative"],
        structured=json.loads(row["structured_json"] or "{}"),
        last_indexed_at=row["last_indexed_at"],
        updated_at=row["updated_at"],
    )


def upsert_digest(conn: sqlite3.Connection, payload: FileDigestIn) -> FileDigest:
    now = iso_now()
    existing = conn.execute(
        "SELECT id FROM file_digests WHERE workspace_id = ? AND file_path = ?",
        (payload.workspace_id, payload.file_path),
    ).fetchone()
    digest_id = existing["id"] if existing is not None else new_id(IdKind.FILE_DIGEST)
    structured_json = json.dumps(payload.structured, sort_keys=True)
    if existing is None:
        conn.execute(
            """
            INSERT INTO file_digests (
                id, workspace_id, file_path, language, chunk_count,
                symbol_count, inbound_edge_count, outbound_edge_count,
                versions_recent, narrative, structured_json,
                last_indexed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                digest_id,
                payload.workspace_id,
                payload.file_path,
                payload.language,
                payload.chunk_count,
                payload.symbol_count,
                payload.inbound_edge_count,
                payload.outbound_edge_count,
                payload.versions_recent,
                payload.narrative,
                structured_json,
                payload.last_indexed_at,
                now,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE file_digests SET
                language = ?, chunk_count = ?, symbol_count = ?,
                inbound_edge_count = ?, outbound_edge_count = ?,
                versions_recent = ?, narrative = ?, structured_json = ?,
                last_indexed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.language,
                payload.chunk_count,
                payload.symbol_count,
                payload.inbound_edge_count,
                payload.outbound_edge_count,
                payload.versions_recent,
                payload.narrative,
                structured_json,
                payload.last_indexed_at,
                now,
                digest_id,
            ),
        )
    return FileDigest(
        id=digest_id,
        workspace_id=payload.workspace_id,
        file_path=payload.file_path,
        language=payload.language,
        chunk_count=payload.chunk_count,
        symbol_count=payload.symbol_count,
        inbound_edge_count=payload.inbound_edge_count,
        outbound_edge_count=payload.outbound_edge_count,
        versions_recent=payload.versions_recent,
        narrative=payload.narrative,
        structured=payload.structured,
        last_indexed_at=payload.last_indexed_at,
        updated_at=now,
    )


def get_digest(conn: sqlite3.Connection, *, workspace_id: str, file_path: str) -> FileDigest | None:
    row = conn.execute(
        "SELECT * FROM file_digests WHERE workspace_id = ? AND file_path = ?",
        (workspace_id, file_path),
    ).fetchone()
    return _row_to_digest(row) if row is not None else None


def list_digests(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int = 50
) -> list[FileDigest]:
    rows = conn.execute(
        "SELECT * FROM file_digests WHERE workspace_id = ? ORDER BY updated_at DESC LIMIT ?",
        (workspace_id, limit),
    ).fetchall()
    return [_row_to_digest(r) for r in rows]
