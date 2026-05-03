"""Single chokepoint for capability invocation + outcome counters.

Both routes that surface capabilities (``list_agent_capabilities`` with
``mark_used=true``) and the workflow harness funnel through here so the
counters cannot drift from reality silently. Every mutation writes an
``audit_log`` row so the source of every count is replayable.

Tables: agent_skills, agent_roles, agent_playbooks. All three share the
same set of maturity columns (migration 0021).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now

CapabilityKind = Literal["skill", "role", "playbook"]
KIND_TO_TABLE: dict[str, str] = {
    "skill": "agent_skills",
    "role": "agent_roles",
    "playbook": "agent_playbooks",
}
SUPPORTED_KINDS: tuple[str, ...] = ("skill", "role", "playbook")


@dataclass(frozen=True, slots=True)
class CapabilityMaturitySnapshot:
    id: str
    name: str
    confidence: float
    usage_count: int
    success_count: int
    failure_count: int
    last_invoked_at: str | None


def _table_for(kind: str) -> str:
    table = KIND_TO_TABLE.get(kind)
    if table is None:
        raise ValueError(f"unsupported capability kind: {kind!r}")
    return table


def record_invocation(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    capability_id: str,
) -> bool:
    """Bump usage_count + last_invoked_at. Returns True on a real update."""
    table = _table_for(kind)
    now_iso = iso_now()
    cursor = conn.execute(
        f"""
        UPDATE {table}
        SET usage_count = usage_count + 1,
            last_invoked_at = ?,
            updated_at = ?
        WHERE id = ? AND workspace_id = ?
        """,
        (now_iso, now_iso, capability_id, workspace_id),
    )
    if cursor.rowcount <= 0:
        return False
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="capability.invocation_recorded",
        target_type=kind,
        target_id=capability_id,
        after={"at": now_iso},
    )
    return True


def record_outcome(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    capability_id: str,
    success: bool,
    episode_id: str | None = None,
) -> bool:
    """Bump success_count or failure_count. Does NOT bump usage_count — the
    caller is expected to have called ``record_invocation`` first when the
    invocation actually happened. Outcome reports without a prior invocation
    are still accepted (e.g. retroactive batch tagging) but only adjust the
    success/failure tally."""
    table = _table_for(kind)
    column = "success_count" if success else "failure_count"
    now_iso = iso_now()
    cursor = conn.execute(
        f"""
        UPDATE {table}
        SET {column} = {column} + 1,
            updated_at = ?
        WHERE id = ? AND workspace_id = ?
        """,
        (now_iso, capability_id, workspace_id),
    )
    if cursor.rowcount <= 0:
        return False
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="capability.outcome_recorded",
        target_type=kind,
        target_id=capability_id,
        source_episode_id=episode_id,
        after={"success": success, "at": now_iso},
    )
    return True


def get_maturity_snapshot(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    capability_id: str,
) -> CapabilityMaturitySnapshot | None:
    """Read the current counters for inspection / hygiene checks."""
    table = _table_for(kind)
    row = conn.execute(
        f"""
        SELECT id, name, confidence, usage_count, success_count, failure_count,
               last_invoked_at
        FROM {table}
        WHERE id = ? AND workspace_id = ?
        """,
        (capability_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    return CapabilityMaturitySnapshot(
        id=str(row["id"]),
        name=str(row["name"]),
        confidence=float(row["confidence"]),
        usage_count=int(row["usage_count"] or 0),
        success_count=int(row["success_count"] or 0),
        failure_count=int(row["failure_count"] or 0),
        last_invoked_at=str(row["last_invoked_at"]) if row["last_invoked_at"] else None,
    )
