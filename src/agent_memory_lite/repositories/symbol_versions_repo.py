"""SQL operations for the ``symbol_versions`` table (1.6.0)."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.symbol_versions import SymbolVersion, SymbolVersionIn
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def _row_to_version(row: sqlite3.Row) -> SymbolVersion:
    return SymbolVersion(
        id=row["id"],
        workspace_id=row["workspace_id"],
        qualified_name=row["qualified_name"],
        file_path=row["file_path"],
        chunk_id=row["chunk_id"],
        language=row["language"],
        signature_text=row["signature_text"],
        signature_hash=row["signature_hash"],
        content_hash=row["content_hash"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def get_latest_version(
    conn: sqlite3.Connection, *, workspace_id: str, qualified_name: str
) -> SymbolVersion | None:
    row = conn.execute(
        "SELECT * FROM symbol_versions "
        "WHERE workspace_id = ? AND qualified_name = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (workspace_id, qualified_name),
    ).fetchone()
    return _row_to_version(row) if row is not None else None


def insert_version(conn: sqlite3.Connection, payload: SymbolVersionIn) -> SymbolVersion:
    version_id = new_id(IdKind.SYMBOL_VERSION)
    created_at = iso_now()
    conn.execute(
        """
        INSERT INTO symbol_versions (
            id, workspace_id, qualified_name, file_path, chunk_id,
            language, signature_text, signature_hash, content_hash,
            created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            payload.workspace_id,
            payload.qualified_name,
            payload.file_path,
            payload.chunk_id,
            payload.language,
            payload.signature_text,
            payload.signature_hash,
            payload.content_hash,
            created_at,
            json.dumps(payload.metadata, sort_keys=True),
        ),
    )
    return SymbolVersion(
        id=version_id,
        workspace_id=payload.workspace_id,
        qualified_name=payload.qualified_name,
        file_path=payload.file_path,
        chunk_id=payload.chunk_id,
        language=payload.language,
        signature_text=payload.signature_text,
        signature_hash=payload.signature_hash,
        content_hash=payload.content_hash,
        created_at=created_at,
        metadata=payload.metadata,
    )


def list_versions_for_qname(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    qualified_name: str,
    limit: int = 50,
) -> list[SymbolVersion]:
    rows = conn.execute(
        "SELECT * FROM symbol_versions "
        "WHERE workspace_id = ? AND qualified_name = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (workspace_id, qualified_name, limit),
    ).fetchall()
    return [_row_to_version(r) for r in rows]
