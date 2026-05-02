"""Read-side queries for the audit_log table.

The audit_log is already populated by every ingest_* / write_*
service (insert_audit). This module exposes the listing surface for
the new POST /memory/list_audit endpoint so an agent can ask
"who/when wrote this decision?", "show every change to this theory
in the last 7 days", etc.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.audit import AuditEntry


def _row_to_audit(row: sqlite3.Row) -> AuditEntry:
    before_raw = row["before_json"]
    after_raw = row["after_json"]
    return AuditEntry(
        id=row["id"],
        workspace_id=row["workspace_id"],
        action=row["action"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        source_episode_id=row["source_episode_id"],
        before=json.loads(before_raw) if before_raw else None,
        after=json.loads(after_raw) if after_raw else None,
        created_at=row["created_at"],
    )


def list_audit_entries(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    target_type: str | None = None,
    target_id: str | None = None,
    action: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> list[AuditEntry]:
    sql = "SELECT * FROM audit_log WHERE workspace_id = ?"
    params: list[object] = [workspace_id]
    if target_type:
        sql += " AND target_type = ?"
        params.append(target_type)
    if target_id:
        sql += " AND target_id = ?"
        params.append(target_id)
    if action:
        sql += " AND action = ?"
        params.append(action)
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    if until:
        sql += " AND created_at <= ?"
        params.append(until)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_audit(row) for row in rows]
