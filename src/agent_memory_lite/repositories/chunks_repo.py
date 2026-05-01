"""SQL operations for the `chunks` table."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.chunks import Chunk, ChunkIn
from agent_memory_lite.models.enums import ChunkKind
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def _row_label(row: sqlite3.Row) -> str | None:
    """Read `label` column if migration 0015 has applied; tolerate older DBs."""
    try:
        value = row["label"]
    except (IndexError, KeyError):
        return None
    return value if value else None


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        id=row["id"],
        workspace_id=row["workspace_id"],
        file_id=row["file_id"],
        episode_id=row["episode_id"],
        kind=ChunkKind(row["kind"]),
        text=row["text"],
        summary=row["summary"],
        label=_row_label(row),
        line_start=row["line_start"],
        line_end=row["line_end"],
        symbols=json.loads(row["symbols_json"] or "[]"),
        embedding_id=row["embedding_id"],
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        created_at=row["created_at"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def insert_chunk(
    conn: sqlite3.Connection,
    chunk_in: ChunkIn,
    *,
    embedding_id: str | None = None,
) -> Chunk:
    chunk_id = new_id(IdKind.CHUNK)
    created_at = iso_now()
    conn.execute(
        """
        INSERT INTO chunks (
            id, workspace_id, file_id, episode_id, kind, text, summary, label,
            line_start, line_end, symbols_json, embedding_id, importance,
            confidence, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chunk_id,
            chunk_in.workspace_id,
            chunk_in.file_id,
            chunk_in.episode_id,
            chunk_in.kind.value,
            chunk_in.text,
            chunk_in.summary,
            chunk_in.label,
            chunk_in.line_start,
            chunk_in.line_end,
            json.dumps(chunk_in.symbols, sort_keys=True),
            embedding_id,
            chunk_in.importance,
            chunk_in.confidence,
            created_at,
            json.dumps(chunk_in.metadata, sort_keys=True),
        ),
    )
    return Chunk(
        id=chunk_id,
        workspace_id=chunk_in.workspace_id,
        file_id=chunk_in.file_id,
        episode_id=chunk_in.episode_id,
        kind=chunk_in.kind,
        text=chunk_in.text,
        summary=chunk_in.summary,
        label=chunk_in.label,
        line_start=chunk_in.line_start,
        line_end=chunk_in.line_end,
        symbols=chunk_in.symbols,
        embedding_id=embedding_id,
        importance=chunk_in.importance,
        confidence=chunk_in.confidence,
        created_at=created_at,
        metadata=chunk_in.metadata,
    )


def get_chunk(conn: sqlite3.Connection, chunk_id: str) -> Chunk | None:
    row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    return _row_to_chunk(row) if row is not None else None


def delete_chunks_by_file(conn: sqlite3.Connection, file_id: str) -> int:
    cur = conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
    return int(cur.rowcount)


def delete_chunks_by_episode(conn: sqlite3.Connection, episode_id: str) -> int:
    cur = conn.execute("DELETE FROM chunks WHERE episode_id = ?", (episode_id,))
    return int(cur.rowcount)


def set_chunk_embedding_id(
    conn: sqlite3.Connection,
    *,
    chunk_id: str,
    embedding_id: str | None,
) -> None:
    conn.execute(
        "UPDATE chunks SET embedding_id = ? WHERE id = ?",
        (embedding_id, chunk_id),
    )


def set_many_chunk_embedding_ids(
    conn: sqlite3.Connection,
    *,
    chunk_ids: list[str],
) -> None:
    conn.executemany(
        "UPDATE chunks SET embedding_id = ? WHERE id = ?",
        [(chunk_id, chunk_id) for chunk_id in chunk_ids],
    )
