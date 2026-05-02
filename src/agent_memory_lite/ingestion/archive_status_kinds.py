"""Status-axis archive helpers (decision / theory / insight).

Pulled out of ``archive_service.py`` so the dispatcher stays under
the SLOC ceiling once prior-status preservation lands. The
service writes an ``archive`` audit entry capturing the
pre-archive status and reads it back on restore so a theory
archived at ``status='supported'`` returns to ``supported``
instead of a generic ``proposed``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.audit_list_repo import list_audit_entries
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now

# (table, archive-set-clause, default restore-status). The actual
# restore prefers the audit-derived prior status; ``fallback`` only
# applies when no archive audit exists.
STATUS_KIND_MAP: dict[str, tuple[str, str, str]] = {
    "decision": (
        "decisions",
        "status='superseded', valid_to=?, updated_at=?",
        "active",
    ),
    "theory": (
        "theories",
        "status='archived', updated_at=?",
        "proposed",
    ),
    "insight": (
        "research_insights",
        "status='archived', updated_at=?",
        "new",
    ),
}


def _read_current_status(
    conn: sqlite3.Connection, *, table: str, object_id: str, workspace_id: str
) -> str | None:
    row = conn.execute(
        f"SELECT status FROM {table} WHERE id = ? AND workspace_id = ?",
        (object_id, workspace_id),
    ).fetchone()
    return row["status"] if row is not None else None


def _restore_target_status(
    conn: sqlite3.Connection,
    *,
    target_type: str,
    object_id: str,
    workspace_id: str,
    fallback: str,
) -> str:
    """Look up the most recent archive audit and use its prior
    status. Falls back to ``fallback`` (the kind's spec default)
    when no audit entry exists, so legacy archives still restore
    safely."""
    entries = list_audit_entries(
        conn,
        workspace_id=workspace_id,
        target_type=target_type,
        target_id=object_id,
        action="archive",
        limit=1,
    )
    if not entries:
        return fallback
    before = entries[0].before or {}
    prior = before.get("status") if isinstance(before, dict) else None
    return str(prior) if isinstance(prior, str) and prior else fallback


def archive_status_kind(
    conn: sqlite3.Connection,
    *,
    kind: str,
    workspace_id: str,
    object_id: str,
    archive: bool,
) -> tuple[bool, str | None]:
    """Flip the status axis for ``kind`` in ``workspace_id``.

    Returns ``(found, prior_status)`` so the caller can report
    success and the restore target can be picked from the audit
    log. Writes an audit entry capturing prior + new status so
    restore can land on the right value.
    """
    table, set_clause, default_restore_status = STATUS_KIND_MAP[kind]
    now = iso_now()
    prior_status = _read_current_status(
        conn, table=table, object_id=object_id, workspace_id=workspace_id
    )
    if archive:
        if kind == "decision":
            params: tuple[object, ...] = (now, now, object_id, workspace_id)
        else:
            params = (now, object_id, workspace_id)
        cur = conn.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = ? AND workspace_id = ?",
            params,
        )
        if cur.rowcount > 0 and prior_status is not None:
            insert_audit(
                conn,
                workspace_id=workspace_id,
                action="archive",
                target_type=kind,
                target_id=object_id,
                before={"status": prior_status},
                after={"status": "archived" if kind != "decision" else "superseded"},
            )
    else:
        target_status = _restore_target_status(
            conn,
            target_type=kind,
            object_id=object_id,
            workspace_id=workspace_id,
            fallback=default_restore_status,
        )
        if kind == "decision":
            unset_sql = (
                f"UPDATE {table} SET status=?, valid_to=NULL, updated_at=? "
                "WHERE id = ? AND workspace_id = ?"
            )
            params = (target_status, now, object_id, workspace_id)
        else:
            unset_sql = (
                f"UPDATE {table} SET status=?, updated_at=? WHERE id = ? AND workspace_id = ?"
            )
            params = (target_status, now, object_id, workspace_id)
        cur = conn.execute(unset_sql, params)
        if cur.rowcount > 0 and prior_status is not None:
            insert_audit(
                conn,
                workspace_id=workspace_id,
                action="restore",
                target_type=kind,
                target_id=object_id,
                before={"status": prior_status},
                after={"status": target_status},
            )
    conn.commit()
    return cur.rowcount > 0, prior_status
