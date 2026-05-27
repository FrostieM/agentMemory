"""Persist v3.1 experiment proposals as memory_candidate rows.

Lifted out of ``experiment_proposal.py`` to keep that module under the
150-SLOC ceiling after the persist-layer addition (audit-round-3 fix).

The writer is intentionally minimal — it only knows how to land a
``MemoryCandidateKind.THEORY_PROPOSAL`` row given an
``ExperimentProposal`` dataclass. Idempotency comes from a
deterministic candidate id derived from the source insight id.

Vector1-audit H1: ``persist_proposal`` distinguishes three outcomes:
* empty string — "skipped" (no source_episode_id, or candidate already
  promoted/rejected so the ON CONFLICT UPDATE was a no-op).
* non-empty string — landed (new insert OR upsert of a still-'new' row).
* exception — the caller sees a hard failure. We do NOT swallow
  ``sqlite3.Error`` here; the route/brain_pass layer logs it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from agent_memory_lite.maintenance.candidates_table import resolve_candidates_table
from agent_memory_lite.utils.time import iso_now

if TYPE_CHECKING:
    from agent_memory_lite.maintenance.experiment_proposal import ExperimentProposal


def candidate_id_for(insight_id: str) -> str:
    """Deterministic candidate id derived from the source insight id."""
    return f"cand_prop_{insight_id}"


def persist_proposal(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    proposal: ExperimentProposal,
) -> str:
    """Write the proposal as a ``memory_candidate(kind=theory_proposal)``.

    Returns the candidate_id when a row was actually written or
    refreshed in the 'new' state. Returns "" when:
      * the proposal has no ``source_episode_id`` (FK skip), OR
      * the conflicting row is already accepted/rejected (the
        ``WHERE status = 'new'`` clause makes the UPDATE a no-op).

    Vector1-audit M1: ON CONFLICT UPDATE is gated by ``status = 'new'``
    so an operator-promoted candidate doesn't silently drift back to
    a heuristic-regenerated body on the next brain_pass.

    Vector1-audit H1: ``sqlite3.IntegrityError`` (FK violation, NOT
    NULL violation) becomes a "skip" return. Other ``sqlite3.Error``
    instances propagate so the caller can log them.
    """
    if not proposal.source_episode_id:
        return ""
    # Final-audit H3: pick the live table at runtime so canonical-only
    # deploys (where ``candidates`` is absent and ``candidates``
    # is the live target) get a successful write instead of a silent
    # OperationalError.
    table = resolve_candidates_table(conn)
    if table is None:
        return ""
    candidate_id = candidate_id_for(proposal.insight_id)
    now = iso_now()
    try:
        cursor = conn.execute(
            f"""
            INSERT INTO {table} (
                id, workspace_id, kind, subject, predicate, object, evidence,
                confidence, importance, trust_level, temporal_json,
                write_targets_json, metadata_json, source_episode_id,
                status, created_at, updated_at
            ) VALUES (?, ?, 'theory_proposal', ?, 'proposes_theory', ?, ?,
                      ?, 0.5, 'agent_observed', '{{}}', '[]', ?, ?,
                      'new', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                object = excluded.object,
                evidence = excluded.evidence,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
                WHERE {table}.status = 'new'
            """,
            (
                candidate_id,
                workspace_id,
                proposal.insight_id,
                proposal.proposal_text,
                proposal.validation_criterion,
                proposal.confidence,
                json.dumps({"source": "experiment_proposal_mvp"}, sort_keys=True),
                proposal.source_episode_id,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        # FK skip / NOT NULL skip — legitimate "this candidate cannot
        # land". Caller treats "" as "no-op", not "error".
        return ""
    # rowcount == 0 means the ON CONFLICT UPDATE matched but the
    # WHERE guard filtered it (status != 'new'). Treat as skip.
    if cursor.rowcount == 0:
        return ""
    return candidate_id
