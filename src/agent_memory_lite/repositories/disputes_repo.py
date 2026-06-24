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
from typing import Any

from agent_memory_lite.repositories.disputes_repo_read import (
    Dispute,
    get_dispute,
    list_disputes,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now

__all__ = [
    "Dispute",
    "get_dispute",
    "insert_dispute",
    "list_disputes",
    "resolve_dispute",
]

_OPEN = "open"
_TERMINAL = frozenset({"accepted", "rejected", "withdrawn"})


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
