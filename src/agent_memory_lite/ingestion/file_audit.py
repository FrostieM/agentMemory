"""Audit helpers for file ingestion."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.audit_repo import insert_audit


def record_file_ingest_audit(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    file_id: str,
    episode_id: str,
    path: str,
    chunks: int,
    edges: int,
    versions: int,
) -> None:
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="ingest_file",
        target_type="file",
        target_id=file_id,
        source_episode_id=episode_id,
        after={"path": path, "chunks": chunks, "edges": edges, "versions": versions},
    )
