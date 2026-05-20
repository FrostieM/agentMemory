"""v3.3 Vector 6 — disputes repository (CRUD over ``memory_disputes``).

Repository-layer SQL wrapper for the inter-agent dispute lifecycle.
All business logic (validation, status transitions, hub-mode write
gating) lives in the service / route layer; this module only owns
the table operations.

Lifecycle states (string column, no FK enum table — kept lean per
project policy):

* ``open``      — propose_dispute just landed it
* ``accepted``  — operator agreed; the target row should be archived
                  / superseded by the calling agent's workflow
* ``rejected``  — operator disagreed; target row stays
* ``withdrawn`` — claimant retracted

Failure-soft on missing table (pre-migration DB): list/get return
empty, write paths raise ``OperationalError`` so the route surface
can return 503 instead of corrupting state.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

_OPEN = "open"
_TERMINAL = frozenset({"accepted", "rejected", "withdrawn"})


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


def insert_dispute(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    target_kind: str,
    target_id: str,
    claimant_agent_id: str,
    claim_text: str,
    evidence: list[Any] | None = None,
) -> str:
    """Land a new dispute in ``open`` status. Returns the dispute id."""
    dispute_id = new_id(IdKind.AUDIT)
    now = iso_now()
    evidence_json = json.dumps(evidence or [], ensure_ascii=False)
    conn.execute(
        """INSERT INTO memory_disputes
           (id, workspace_id, target_kind, target_id, claimant_agent_id,
            claim_text, evidence_json, status, resolution,
            created_at, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)""",
        (
            dispute_id,
            workspace_id,
            target_kind,
            target_id,
            claimant_agent_id,
            claim_text,
            evidence_json,
            _OPEN,
            now,
        ),
    )
    conn.commit()
    return dispute_id


def resolve_dispute(
    conn: sqlite3.Connection,
    *,
    dispute_id: str,
    new_status: str,
    resolution: str = "",
) -> bool:
    """Move a dispute to a terminal state.

    Returns True when an open row was actually flipped; False when the
    id is unknown or the row was already terminal (caller decides
    whether that's an error). The check-and-flip is atomic via the
    WHERE status='open' guard so concurrent resolves don't double-write.
    """
    if new_status not in _TERMINAL:
        raise ValueError(f"new_status must be one of {sorted(_TERMINAL)}")
    now = iso_now()
    cur = conn.execute(
        """UPDATE memory_disputes
              SET status = ?, resolution = ?, resolved_at = ?
            WHERE id = ? AND status = ?""",
        (new_status, resolution, now, dispute_id, _OPEN),
    )
    conn.commit()
    return cur.rowcount > 0


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
