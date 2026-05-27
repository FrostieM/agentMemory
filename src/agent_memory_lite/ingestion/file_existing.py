"""Helpers for unchanged file ingest rows."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.enums import TrustLevel
from agent_memory_lite.models.files import FileRecord
from agent_memory_lite.repositories.files_repo import get_file_by_path, upsert_file_row


def refresh_existing_file_metadata(
    conn: sqlite3.Connection,
    *,
    existing: FileRecord,
    workspace_id: str,
    path: str,
    language: str | None,
    content_hash: str,
    size_bytes: int,
    timestamp: str,
) -> FileRecord:
    with with_tx(conn):
        upsert_file_row(
            conn,
            file_id=existing.id,
            workspace_id=workspace_id,
            path=path,
            language=language,
            content_hash=content_hash,
            size_bytes=size_bytes,
            metadata={"trust_level": TrustLevel.UNTRUSTED_DOC.value},
            timestamp=timestamp,
        )
    return get_file_by_path(conn, workspace_id=workspace_id, path=path) or existing
