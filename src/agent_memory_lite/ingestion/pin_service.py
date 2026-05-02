"""``memory_pin`` dispatcher.

Pinned decisions / behavior instructions / core memory entries stay
in the active context envelope regardless of query relevance or token
budget. Architecturally this mirrors the archive-service shape: one
callable that knows the right column per kind and writes via the
per-kind table. Roles / skills / playbooks intentionally stay
un-pinned — they already ride the capability ranker which surfaces
them by query relevance and intent. Pinning a role would force it
into every cycle, typically not what the operator wants.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.repositories.decisions_repo import set_decision_pinned
from agent_memory_lite.utils.time import iso_now

# kind → physical table name. Decisions go through their own helper
# (set_decision_pinned) because the row lifecycle includes status /
# valid_to bookkeeping; everything else is a plain ``UPDATE pinned``.
_TABLE_BY_KIND: dict[str, str] = {
    "behavior_instruction": "behavior_instructions",
    "core_memory": "core_memory",
}


def _set_table_pinned(
    conn: sqlite3.Connection,
    *,
    table: str,
    object_id: str,
    workspace_id: str,
    pinned: bool,
) -> bool:
    """Flip ``pinned`` for a row in ``table`` scoped to the workspace."""
    cur = conn.execute(
        f"UPDATE {table} SET pinned = ?, updated_at = ? WHERE id = ? AND workspace_id = ?",
        (1 if pinned else 0, iso_now(), object_id, workspace_id),
    )
    conn.commit()
    return cur.rowcount > 0


def pin_memory_object(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    object_id: str,
    pinned: bool = True,
) -> dict[str, object]:
    """Flip the pinned flag for an object in ``workspace_id``."""
    kind = (kind or "").lower().strip()
    if kind == "decision":
        ok = set_decision_pinned(
            conn,
            decision_id=object_id,
            workspace_id=workspace_id,
            pinned=pinned,
            updated_at=iso_now(),
        )
        return {"kind": kind, "id": object_id, "pinned": pinned, "found": ok}
    if kind in _TABLE_BY_KIND:
        ok = _set_table_pinned(
            conn,
            table=_TABLE_BY_KIND[kind],
            object_id=object_id,
            workspace_id=workspace_id,
            pinned=pinned,
        )
        return {"kind": kind, "id": object_id, "pinned": pinned, "found": ok}
    raise ValueError(
        f"unsupported pin kind: {kind!r}; pinning supports: "
        "decision, behavior_instruction, core_memory"
    )
