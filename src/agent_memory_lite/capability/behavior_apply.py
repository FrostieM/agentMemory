"""Track when behavior instructions are rendered into a context envelope.

`behaviors.application_count` and `last_applied_at` are written through this
single batched function. v1.5 wired the counters
through a single batched function, called once per envelope build when the
``MEMORY_BEHAVIOR_APPLY_TRACKING_ENABLED`` flag is on.

Single audit row covers the whole batch to keep audit_log volume bounded
even at high envelope-build frequencies (plan blind spot #5).
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now


def mark_behavior_instructions_applied(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    instruction_ids: list[str],
) -> int:
    """Bump application_count + last_applied_at for a batch of instructions.

    Returns the count of rows actually updated.
    """
    if not instruction_ids:
        return 0
    now_iso = iso_now()
    placeholders = ",".join("?" for _ in instruction_ids)
    try:
        cursor = conn.execute(
            f"""
            UPDATE behaviors
            SET application_count = application_count + 1,
                last_applied_at = ?,
                updated_at = ?
            WHERE workspace_id = ? AND id IN ({placeholders})
            """,
            (now_iso, now_iso, workspace_id, *instruction_ids),
        )
    except sqlite3.OperationalError:
        # Partial schema without application_count / last_applied_at.
        # Tracking is opt-in, never required for correctness; treat as no-op.
        return 0
    updated = int(cursor.rowcount or 0)
    if updated <= 0:
        return 0
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="instruction.applied",
        target_type="behavior_instruction",
        target_id=workspace_id,
        after={"applied_ids": list(instruction_ids), "count": updated, "at": now_iso},
    )
    return updated
