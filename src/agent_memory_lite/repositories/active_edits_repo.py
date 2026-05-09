"""SQL operations for the ``active_edits`` table (1.7.0)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.models.active_edits import ActiveEdit, ActiveEditIn
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def _row_to_edit(row: sqlite3.Row) -> ActiveEdit:
    return ActiveEdit(
        id=row["id"],
        workspace_id=row["workspace_id"],
        qualified_name=row["qualified_name"],
        file_path=row["file_path"],
        agent_id=row["agent_id"],
        claimed_at=row["claimed_at"],
        expires_at=row["expires_at"],
        note=row["note"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def insert_claim(conn: sqlite3.Connection, payload: ActiveEditIn) -> ActiveEdit:
    edit_id = new_id(IdKind.ACTIVE_EDIT)
    claimed_at = iso_now()
    expires_at = (datetime.now(UTC) + timedelta(minutes=payload.ttl_minutes)).isoformat()
    conn.execute(
        """
        INSERT INTO active_edits (
            id, workspace_id, qualified_name, file_path, agent_id,
            claimed_at, expires_at, note, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edit_id,
            payload.workspace_id,
            payload.qualified_name,
            payload.file_path,
            payload.agent_id,
            claimed_at,
            expires_at,
            payload.note,
            json.dumps(payload.metadata, sort_keys=True),
        ),
    )
    return ActiveEdit(
        id=edit_id,
        workspace_id=payload.workspace_id,
        qualified_name=payload.qualified_name,
        file_path=payload.file_path,
        agent_id=payload.agent_id,
        claimed_at=claimed_at,
        expires_at=expires_at,
        note=payload.note,
        metadata=payload.metadata,
    )


def find_active_claim(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    qualified_name: str | None,
    file_path: str | None,
) -> ActiveEdit | None:
    """Return the most recent NON-EXPIRED claim matching the target."""
    now_iso = iso_now()
    if qualified_name:
        row = conn.execute(
            "SELECT * FROM active_edits "
            "WHERE workspace_id = ? AND qualified_name = ? AND expires_at > ? "
            "ORDER BY claimed_at DESC LIMIT 1",
            (workspace_id, qualified_name, now_iso),
        ).fetchone()
    elif file_path:
        row = conn.execute(
            "SELECT * FROM active_edits "
            "WHERE workspace_id = ? AND file_path = ? "
            "AND qualified_name IS NULL AND expires_at > ? "
            "ORDER BY claimed_at DESC LIMIT 1",
            (workspace_id, file_path, now_iso),
        ).fetchone()
    else:
        return None
    return _row_to_edit(row) if row is not None else None


def list_active_claims(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int = 100
) -> list[ActiveEdit]:
    rows = conn.execute(
        "SELECT * FROM active_edits "
        "WHERE workspace_id = ? AND expires_at > ? "
        "ORDER BY claimed_at DESC LIMIT ?",
        (workspace_id, iso_now(), limit),
    ).fetchall()
    return [_row_to_edit(r) for r in rows]


def delete_claim(conn: sqlite3.Connection, *, claim_id: str) -> int:
    cur = conn.execute("DELETE FROM active_edits WHERE id = ?", (claim_id,))
    return int(cur.rowcount)


def cleanup_expired(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    cur = conn.execute(
        "DELETE FROM active_edits WHERE workspace_id = ? AND expires_at <= ?",
        (workspace_id, iso_now()),
    )
    return int(cur.rowcount)
