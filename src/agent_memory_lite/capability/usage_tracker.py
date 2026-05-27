"""Single chokepoint for capability invocation + outcome counters.

Capability search/invocation flows and the workflow harness funnel through
here so counters cannot drift from reality silently. Every mutation writes an
``audit_log`` row so the source of every count is replayable.

Canonical table: skills, with subtype=skill/role/playbook.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now

CapabilityKind = Literal["skill", "role", "playbook"]
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


def _ensure_supported(kind: str) -> None:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"unsupported capability kind: {kind!r}")


def record_invocation(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    kind: str,
    capability_id: str,
) -> bool:
    """Bump usage_count + last_invoked_at. Returns True on a real update."""
    _ensure_supported(kind)
    now_iso = iso_now()
    cursor = conn.execute(
        """
        UPDATE skills
        SET usage_count = usage_count + 1,
            last_invoked_at = ?,
            updated_at = ?
        WHERE id = ? AND workspace_id = ? AND subtype = ?
        """,
        (now_iso, now_iso, capability_id, workspace_id, kind),
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
    _ensure_supported(kind)
    column = "success_count" if success else "failure_count"
    now_iso = iso_now()
    cursor = conn.execute(
        f"""
        UPDATE skills
        SET {column} = {column} + 1,
            updated_at = ?
        WHERE id = ? AND workspace_id = ? AND subtype = ?
        """,
        (now_iso, capability_id, workspace_id, kind),
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
    _ensure_supported(kind)
    row = conn.execute(
        """
        SELECT id, name, confidence, usage_count, success_count, failure_count,
               last_invoked_at
        FROM skills
        WHERE id = ? AND workspace_id = ? AND subtype = ?
        """,
        (capability_id, workspace_id, kind),
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
