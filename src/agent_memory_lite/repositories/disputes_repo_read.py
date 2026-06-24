"""v3.3 Vector 6 — disputes repository read surface.

Read-side concern split out of ``disputes_repo``: the ``Dispute`` read
shape, the row-to-dataclass mapper, and the failure-soft list/get
queries. The parent module re-exports every symbol here, so the public
import path (``disputes_repo``) is unchanged.

Failure-soft on missing table (pre-migration DB): list/get return
empty so the route surface can degrade gracefully.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Dispute:
    """One row from ``memory_disputes`` — full read shape."""

    id: str
    workspace_id: str
    target_kind: str
    target_id: str
    claimant_agent_id: str
    claim_text: str
    evidence: list[Any]
    status: str
    resolution: str
    created_at: str
    resolved_at: str


def _row_to_dispute(row: sqlite3.Row) -> Dispute:
    raw_ev = row["evidence_json"] or "[]"
    try:
        ev = json.loads(raw_ev)
    except (ValueError, TypeError):
        ev = []
    if not isinstance(ev, list):
        ev = []
    return Dispute(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        target_kind=str(row["target_kind"]),
        target_id=str(row["target_id"]),
        claimant_agent_id=str(row["claimant_agent_id"]),
        claim_text=str(row["claim_text"]),
        evidence=ev,
        status=str(row["status"]),
        resolution=str(row["resolution"] or ""),
        created_at=str(row["created_at"]),
        resolved_at=str(row["resolved_at"] or ""),
    )


def list_disputes(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    status: str | None = None,
    limit: int = 50,
) -> list[Dispute]:
    """Return disputes for the workspace, newest first. ``status`` is
    an optional filter; pass None to get every state."""
    try:
        if status:
            rows = conn.execute(
                """SELECT * FROM memory_disputes
                    WHERE workspace_id = ? AND status = ?
                 ORDER BY created_at DESC LIMIT ?""",
                (workspace_id, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM memory_disputes
                    WHERE workspace_id = ?
                 ORDER BY created_at DESC LIMIT ?""",
                (workspace_id, limit),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_dispute(r) for r in rows]


def get_dispute(conn: sqlite3.Connection, *, dispute_id: str) -> Dispute | None:
    """Read one dispute by id. Returns None on miss / missing table."""
    try:
        row = conn.execute("SELECT * FROM memory_disputes WHERE id = ?", (dispute_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return _row_to_dispute(row) if row else None
