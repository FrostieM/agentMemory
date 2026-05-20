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


def _row_optional_str(row: sqlite3.Row, column: str) -> str | None:
    """Tolerate pre-migration DBs that lack the requested column."""
    try:
        value = row[column]
    except (IndexError, KeyError):
        return None
    return value if value else None


def _coerce_chunk_kind(raw: object) -> ChunkKind:
    """Convert a raw DB ``kind`` string to the enum, tolerating values
    the enum does not yet know about.

    The code indexer (v1.4 → v2.1.x) historically inserted bare strings
    (``'block'``, ``'symbol'``) that were not registered as enum values
    until later — and once one such row landed, every
    ``/memory/get_context`` call that gathered it raised ``ValueError``
    and the route returned HTTP 500. Two defenses now cover that:

    1. ``BLOCK`` / ``SYMBOL`` are first-class enum values (added in
       the same patch as this helper).
    2. Any other unknown string a future writer might introduce falls
       back to ``DOC`` instead of crashing the read path. ``DOC`` is
       the safest default because the brief / context renderer treats
       it as plain prose text — degraded ranking, never wrong output.
    """
    if isinstance(raw, ChunkKind):
        return raw
    try:
        return ChunkKind(str(raw))
    except ValueError:
        return ChunkKind.DOC


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        id=row["id"],
        workspace_id=row["workspace_id"],
        file_id=row["file_id"],
        episode_id=row["episode_id"],
        kind=_coerce_chunk_kind(row["kind"]),
        text=row["text"],
        summary=row["summary"],
        label=_row_label(row),
        line_start=row["line_start"],
        line_end=row["line_end"],
        symbols=json.loads(row["symbols_json"] or "[]"),
        symbol_kind=_row_optional_str(row, "symbol_kind"),
        qualified_name=_row_optional_str(row, "qualified_name"),
        parent_qualified_name=_row_optional_str(row, "parent_qualified_name"),
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
            confidence, created_at, metadata_json,
            symbol_kind, qualified_name, parent_qualified_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            chunk_in.symbol_kind,
            chunk_in.qualified_name,
            chunk_in.parent_qualified_name,
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
        symbol_kind=chunk_in.symbol_kind,
        qualified_name=chunk_in.qualified_name,
        parent_qualified_name=chunk_in.parent_qualified_name,
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


def list_chunk_ids_for_file(conn: sqlite3.Connection, file_id: str) -> list[str]:
    """Return every chunk id whose file_id matches. Used by the file
    ingest pipeline to drop dependent rows (FTS, symbol_edges) before
    the chunks themselves are deleted on re-ingest.
    """
    rows = conn.execute("SELECT id FROM chunks WHERE file_id = ?", (file_id,)).fetchall()
    return [str(r["id"]) for r in rows]


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
