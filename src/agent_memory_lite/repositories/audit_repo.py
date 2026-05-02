"""SQL operations for the `audit_log` table."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.audit import AuditEntry
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def insert_audit(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    action: str,
    target_type: str,
    target_id: str,
    source_episode_id: str | None = None,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> AuditEntry:
    entry_id = new_id(IdKind.AUDIT)
    created_at = iso_now()
    conn.execute(
        """
        INSERT INTO audit_log (
            id, workspace_id, action, target_type, target_id,
            source_episode_id, before_json, after_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            workspace_id,
            action,
            target_type,
            target_id,
            source_episode_id,
            None if before is None else json.dumps(before, sort_keys=True),
            None if after is None else json.dumps(after, sort_keys=True),
            created_at,
        ),
    )
    return AuditEntry(
        id=entry_id,
        workspace_id=workspace_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        source_episode_id=source_episode_id,
        before=before,
        after=after,
        created_at=created_at,
    )
