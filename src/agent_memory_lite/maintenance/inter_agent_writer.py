"""v3.1 Vector 6 — persist negotiation conflicts as memory_candidate.

Lifted out of ``inter_agent.py`` so the discovery module stays under
the 150-SLOC ceiling. Same writer pattern as
``experiment_proposal_writer`` and ``predictive_failure_writer``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from agent_memory_lite.maintenance.candidates_table import resolve_candidates_table
from agent_memory_lite.utils.time import iso_now

if TYPE_CHECKING:
    from agent_memory_lite.maintenance.inter_agent import InterAgentBelief


def candidate_id_for(foreign_id: str, local_id: str) -> str:
    return f"cand_neg_{foreign_id}__{local_id}"


def persist_conflict(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    foreign: InterAgentBelief,
    local_id: str,
    local_text: str,
    source_episode_id: str,
) -> str:
    """Land one negotiation candidate. Returns the id when newly
    inserted, "" when skipped (no episode, schema missing, or row
    already promoted)."""
    if not source_episode_id:
        return ""
    table = resolve_candidates_table(conn)
    if table is None:
        return ""
    cid = candidate_id_for(foreign.foreign_id, local_id)
    now = iso_now()
    reason = (
        f"Foreign agent {foreign.foreign_workspace_id} holds a different "
        f"body for {foreign.title!r}. "
        f"Local: {local_text[:80]}... Foreign: {foreign.decision_text[:80]}..."
    )
    try:
        cursor = conn.execute(
            f"""INSERT INTO {table} (
                id, workspace_id, kind, subject, predicate, object, evidence,
                confidence, importance, trust_level, temporal_json,
                write_targets_json, metadata_json, source_episode_id,
                status, created_at, updated_at
            ) VALUES (?, ?, 'negotiation', ?, 'disagrees_with', ?, ?,
                      ?, 0.5, 'agent_observed', '{{}}', '[]', ?, ?,
                      'new', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                evidence = excluded.evidence,
                updated_at = excluded.updated_at
                WHERE {table}.status = 'new'
            """,
            (
                cid,
                workspace_id,
                local_id,
                foreign.foreign_id,
                reason,
                float(foreign.confidence),
                json.dumps(
                    {
                        "source": "inter_agent_mvp",
                        "foreign_workspace": foreign.foreign_workspace_id,
                    },
                    sort_keys=True,
                ),
                source_episode_id,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        return ""
    if cursor.rowcount == 0:
        return ""
    return cid
