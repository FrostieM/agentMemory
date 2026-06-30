"""SQL operations for the `audit_log` table."""

from __future__ import annotations

import json
import logging
import sqlite3

from agent_memory_lite.api.agent_context import current_agent_id
from agent_memory_lite.models.audit import AuditEntry
from agent_memory_lite.redaction.payload import redact_freetext_fields
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

logger = logging.getLogger(__name__)


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
    # round-B: redact before/after at THIS single chokepoint so EVERY audit write is
    # secret-safe regardless of caller. write_canonical + edit redacted their payloads,
    # but the low-level storage.writer.write() path did NOT -- a direct write() leaked
    # secrets into audit_log.after_json. redact_freetext_fields is idempotent, so
    # double-redacting an already-clean payload is a no-op.
    before = None if before is None else redact_freetext_fields(before)
    after = None if after is None else redact_freetext_fields(after)
    # default=str: a non-serializable payload value must not crash the operation being audited.
    before_json = None if before is None else json.dumps(before, sort_keys=True, default=str)
    after_json = None if after is None else json.dumps(after, sort_keys=True, default=str)
    # Use a try/except fallback so partial audit schemas without agent_id
    # still write without failing; agent_id is silently dropped in that case.
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
                before_json,
                after_json,
                created_at,
                agent_id,
            ),
        )
    except sqlite3.Error:
        # The full insert failed -- usually a schema missing agent_id; retry
        # without it. Catching the sqlite base class (not just OperationalError)
        # routes any other failure to the best-effort fallback below instead of
        # crashing the operation being audited.
        try:
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
                    before_json,
                    after_json,
                    created_at,
                ),
            )
        except sqlite3.Error as exc:
            # The retry failed too — audit_log is absent (pre-migration /
            # partially built DB) or unwritable (locked, I/O error,
            # corrupt image). An audit-trail write must never crash the
            # operation it audits, so the row is dropped best-effort —
            # but logged, so the gap is visible rather than silent.
            logger.warning("audit_log write skipped (%s): %s", action, exc)
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
