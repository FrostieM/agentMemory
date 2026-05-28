"""SQL operations for the `chunks` table."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from agent_memory_lite.models.chunks import Chunk, ChunkIn
from agent_memory_lite.models.enums import ChunkKind, coerce_enum
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

EMBEDDING_TEXT_SHA256_KEY = "embedding_text_sha256"
EMBEDDING_STALE_REASON_KEY = "embedding_stale_reason"


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
    """Thin wrapper that pins the safe fallback for chunks.kind.

    See ``models.enums.coerce_enum`` for the underlying contract and
    historical context (code-indexer raw-SQL ``'block'`` / ``'symbol'``
    writes crashed the read path before the enum caught up). ``DOC``
    is the safest fallback — the brief / context renderer treats it
    as plain prose text, so an unknown future kind only degrades
    ranking, never produces wrong output.
    """
    return coerce_enum(ChunkKind, raw, ChunkKind.DOC)


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
        # v3.5 sector-2 audit-followup: one malformed JSON cell would
        # crash every read on chunks (and any envelope that gathered it).
        # Tolerant ``_safe_json_*`` falls back to [] / {} on parse error.
        symbols=_safe_json_list(row["symbols_json"]),
        symbol_kind=_row_optional_str(row, "symbol_kind"),
        qualified_name=_row_optional_str(row, "qualified_name"),
        parent_qualified_name=_row_optional_str(row, "parent_qualified_name"),
        embedding_id=row["embedding_id"],
        # v3.5 sector-2 audit-followup: NULL or non-numeric importance/
        # confidence would raise TypeError/ValueError inside float() and
        # crash the parser. Default to 0.0 so the row still loads.
        importance=_safe_float(row["importance"]),
        confidence=_safe_float(row["confidence"]),
        created_at=row["created_at"],
        metadata=_safe_json_dict(row["metadata_json"]),
    )


def _safe_float(value: object, default: float = 0.0) -> float:
    """Coerce to float, fall back to ``default`` on NULL / type error."""
    if value is None:
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _safe_json_list(raw: object) -> list[object]:
    """JSON-decode a list column, falling back to ``[]`` on any error."""
    if not raw:
        return []
    try:
        data = json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _safe_json_dict(raw: object) -> dict[str, object]:
    """JSON-decode a dict column, falling back to ``{}`` on any error."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def chunk_text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _metadata_json_with_embedding_hash(raw: object, text_hash: str | None) -> str:
    metadata = _safe_json_dict(raw)
    if text_hash is None:
        metadata.pop(EMBEDDING_TEXT_SHA256_KEY, None)
    else:
        metadata[EMBEDDING_TEXT_SHA256_KEY] = text_hash
        metadata.pop(EMBEDDING_STALE_REASON_KEY, None)
    return json.dumps(metadata, sort_keys=True)


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
    embedding_text_hash: str | None = None,
) -> None:
    if embedding_text_hash is not None or embedding_id is None:
        row = conn.execute(
            "SELECT metadata_json FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE chunks SET embedding_id = ?, metadata_json = ? WHERE id = ?",
            (
                embedding_id,
                _metadata_json_with_embedding_hash(row["metadata_json"], embedding_text_hash),
                chunk_id,
            ),
        )
        return
    conn.execute(
        "UPDATE chunks SET embedding_id = ? WHERE id = ?",
        (embedding_id, chunk_id),
    )


def set_many_chunk_embedding_ids(
    conn: sqlite3.Connection,
    *,
    chunk_ids: list[str],
    embedding_text_hashes: dict[str, str] | None = None,
) -> None:
    if not chunk_ids:
        return
    if embedding_text_hashes:
        rows = conn.execute(
            f"""
            SELECT id, metadata_json
            FROM chunks
            WHERE id IN ({",".join("?" for _ in chunk_ids)})
            """,
            chunk_ids,
        ).fetchall()
        conn.executemany(
            "UPDATE chunks SET embedding_id = ?, metadata_json = ? WHERE id = ?",
            [
                (
                    str(row["id"]),
                    _metadata_json_with_embedding_hash(
                        row["metadata_json"],
                        embedding_text_hashes.get(str(row["id"])),
                    ),
                    str(row["id"]),
                )
                for row in rows
            ],
        )
        return
    conn.executemany(
        "UPDATE chunks SET embedding_id = ? WHERE id = ?",
        [(chunk_id, chunk_id) for chunk_id in chunk_ids],
    )


def mark_chunk_embedding_stale(
    conn: sqlite3.Connection,
    *,
    chunk_id: str,
    previous_text: str | None = None,
    reason: str = "chunk_text_changed",
) -> None:
    row = conn.execute(
        "SELECT metadata_json FROM chunks WHERE id = ?",
        (chunk_id,),
    ).fetchone()
    if row is None:
        return
    metadata = _safe_json_dict(row["metadata_json"])
    if previous_text is not None and EMBEDDING_TEXT_SHA256_KEY not in metadata:
        metadata[EMBEDDING_TEXT_SHA256_KEY] = chunk_text_sha256(previous_text)
    metadata[EMBEDDING_STALE_REASON_KEY] = reason
    conn.execute(
        "UPDATE chunks SET metadata_json = ? WHERE id = ?",
        (json.dumps(metadata, sort_keys=True), chunk_id),
    )
