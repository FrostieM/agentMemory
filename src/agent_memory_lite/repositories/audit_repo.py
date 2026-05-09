"""SQL operations for the `audit_log` table."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.api.agent_context import current_agent_id
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
    agent_id: str | None = None,
) -> AuditEntry:
    """Insert one audit-log row.

    1.3.0: ``agent_id`` is sourced from the request-scoped ContextVar
    (set by ``AgentIdentityMiddleware`` on incoming HTTP requests) when
    not supplied explicitly. Pass ``agent_id`` directly for
    out-of-request callers (cron jobs, tests, MCP stdio handlers).
    NULL is preserved for back-compat with pre-1.3.0 rows.
    """
    if agent_id is None:
        agent_id = current_agent_id()
    entry_id = new_id(IdKind.AUDIT)
    created_at = iso_now()
    # 1.3.0 schema migration 0026 adds the ``agent_id`` column. Use a
    # try/except fallback so pre-migration databases (rare, only legacy
    # workspaces that haven't applied 0026 yet) still write without
    # failing — agent_id is silently dropped in that case.
    try:
        conn.execute(
            """
            INSERT INTO audit_log (
                id, workspace_id, action, target_type, target_id,
                source_episode_id, before_json, after_json, created_at,
                agent_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                agent_id,
            ),
        )
    except sqlite3.OperationalError:
        # Legacy schema without agent_id column.
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
