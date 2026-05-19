"""v3.1 Vector 6 MVP — inter-agent negotiation primitives.

Two-agent belief exchange: one agent EXPORTS a snapshot of its
decisions (with confidence + outcome_score) as portable JSON; the
other agent IMPORTS it and surfaces conflicts where its local
decision disagrees with the foreign one.

Conflicts land as ``memory_candidate(kind='negotiation')`` rows for
the operator to review at /ui/review. Nothing auto-overrides local
state — the operator decides whether to promote, reject, or
supersede.

# Contract

* ``export_workspace`` — read-only, returns a list of
  ``InterAgentBelief`` dataclasses. Caller serializes (the MVP doesn't
  prescribe a wire format).
* ``import_beliefs`` — takes the dataclasses + a local connection,
  detects pairs where local has a same-title decision with conflicting
  body, and lands ``memory_candidate`` rows.
* Idempotent via deterministic candidate id
  ``cand_neg_{foreign_id}__{local_id}``.

# Settings

* ``MEMORY_INTER_AGENT_ENABLED`` — default ``false``. Gates the entire
  module to avoid surprise cross-workspace writes.
* ``MEMORY_INTER_AGENT_MIN_TITLE_LEN`` — default ``8``. Conflicts only
  emit when both titles have ≥ N chars (filter "untitled" pairs).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.maintenance.inter_agent_writer import persist_conflict


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


# Live-validation-2026-05-20: removed the is_enabled() master gate.
# Inter-agent functions are NEVER auto-invoked (no brain_pass step,
# no implicit caller) — they only run when an explicit operator or
# tool calls them. A toggle on a function-call-only feature is dead
# weight: the operator already gates by choosing whether to call.
def min_title_len() -> int:
    return _int_env("MEMORY_INTER_AGENT_MIN_TITLE_LEN", 8, floor=1)


@dataclass(frozen=True, slots=True)
class InterAgentBelief:
    """Portable shape of one decision exported by a foreign agent.

    Keeps only the fields needed for conflict detection. The full
    decision body stays with the exporting agent — the importer's job
    is to flag disagreement, not to mirror state.
    """

    foreign_id: str
    title: str
    decision_text: str
    confidence: float
    outcome_score: float
    foreign_workspace_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "foreign_id": self.foreign_id,
            "title": self.title,
            "decision_text": self.decision_text,
            "confidence": self.confidence,
            "outcome_score": self.outcome_score,
            "foreign_workspace_id": self.foreign_workspace_id,
        }


def export_workspace(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int = 100
) -> list[InterAgentBelief]:
    """Return active decisions as portable beliefs. Read-only."""
    try:
        rows = conn.execute(
            "SELECT id, COALESCE(title, '') AS title, "
            "COALESCE(decision_text, '') AS decision_text, "
            "COALESCE(confidence, 0.0) AS confidence, "
            "COALESCE(outcome_score, 0.0) AS outcome_score "
            "FROM decisions WHERE workspace_id = ? AND status = 'active' "
            "ORDER BY updated_at DESC LIMIT ?",
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        InterAgentBelief(
            foreign_id=str(r["id"]),
            title=str(r["title"]),
            decision_text=str(r["decision_text"]),
            confidence=float(r["confidence"]),
            outcome_score=float(r["outcome_score"]),
            foreign_workspace_id=workspace_id,
        )
        for r in rows
    ]


def _find_local_conflict(
    conn: sqlite3.Connection, workspace_id: str, foreign: InterAgentBelief
) -> tuple[str, str] | None:
    """Return (local_id, local_text) if local has a same-title decision
    with a different body. Returns ``None`` when no conflict."""
    try:
        row = conn.execute(
            "SELECT id, COALESCE(decision_text, '') AS decision_text "
            "FROM decisions WHERE workspace_id = ? AND status = 'active' "
            "AND title = ? ORDER BY updated_at DESC LIMIT 1",
            (workspace_id, foreign.title),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    local_text = str(row["decision_text"])
    if local_text.strip() == foreign.decision_text.strip():
        return None
    return str(row["id"]), local_text


def import_beliefs(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    beliefs: list[InterAgentBelief],
    source_episode_id: str,
) -> int:
    """Detect conflicts and persist them as ``memory_candidates``.

    Returns the count of candidate rows landed. Idempotent via
    deterministic candidate id.
    """
    if not beliefs or not source_episode_id:
        return 0
    floor = min_title_len()
    landed = 0
    for foreign in beliefs:
        if len(foreign.title) < floor:
            continue
        conflict = _find_local_conflict(conn, workspace_id, foreign)
        if conflict is None:
            continue
        local_id, local_text = conflict
        cid = persist_conflict(
            conn,
            workspace_id=workspace_id,
            foreign=foreign,
            local_id=local_id,
            local_text=local_text,
            source_episode_id=source_episode_id,
        )
        if cid:
            landed += 1
    conn.commit()
    return landed
