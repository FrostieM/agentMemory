"""SQL operations for the `core_memory` table."""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.core_memory import CoreMemory


def _row_to_core(row: sqlite3.Row) -> CoreMemory:
    return CoreMemory(
        id=row["id"],
        workspace_id=row["workspace_id"],
        key=row["key"],
        value=row["value"],
        source_episode_id=row["source_episode_id"],
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_core_memory_row(
    conn: sqlite3.Connection,
    *,
    core_id: str,
    workspace_id: str,
    key: str,
    value: str,
    source_episode_id: str | None,
    confidence: float,
    importance: float,
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO core_memory (
            id, workspace_id, key, value, source_episode_id,
            confidence, importance, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            value = excluded.value,
            confidence = excluded.confidence,
            importance = excluded.importance,
            updated_at = excluded.updated_at
        """,
        (
            core_id,
            workspace_id,
            key,
            value,
            source_episode_id,
            confidence,
            importance,
            timestamp,
            timestamp,
        ),
    )


def deactivate_for_key(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    key: str,
    timestamp: str,
    keep_id: str | None = None,
) -> None:
    if keep_id is None:
        conn.execute(
            """
            UPDATE core_memory
            SET active = 0, updated_at = ?
            WHERE workspace_id = ? AND key = ? AND active = 1
            """,
            (timestamp, workspace_id, key),
        )
    else:
        conn.execute(
            """
            UPDATE core_memory
            SET active = 0, updated_at = ?
            WHERE workspace_id = ? AND key = ? AND active = 1 AND id != ?
            """,
            (timestamp, workspace_id, key, keep_id),
        )


def list_active_core(conn: sqlite3.Connection, workspace_id: str) -> list[CoreMemory]:
    rows = conn.execute(
        """
        SELECT * FROM core_memory
        WHERE workspace_id = ? AND active = 1
        ORDER BY importance DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [_row_to_core(r) for r in rows]


def get_active_by_key(conn: sqlite3.Connection, workspace_id: str, key: str) -> CoreMemory | None:
    row = conn.execute(
        """
        SELECT * FROM core_memory
        WHERE workspace_id = ? AND key = ? AND active = 1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (workspace_id, key),
    ).fetchone()
    return _row_to_core(row) if row is not None else None
