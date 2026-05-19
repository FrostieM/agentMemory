"""Persist v3.1 predictive-failure warnings as memory_candidate rows.

Symmetric with ``experiment_proposal_writer.persist_proposal``. Lifted
out of ``predictive_failure.py`` so that module stays under the 150-
SLOC ceiling and the persistence story is reviewable independently.

A persisted warning lands in the operator's standard review queue
(``/ui/review`` + ``memory_review_queue``) so the v3.1 Vector 5
output reaches a human surface without any UI work.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from agent_memory_lite.maintenance.candidates_table import resolve_candidates_table
from agent_memory_lite.utils.time import iso_now

if TYPE_CHECKING:
    from agent_memory_lite.maintenance.predictive_failure import PredictiveWarning


def candidate_id_for(fresh_id: str, failed_id: str) -> str:
    """Deterministic candidate id derived from the warning pair.

    Both ids participate so a fresh decision that overlaps two
    different failures produces two distinct candidates (one per
    pair) — useful when the operator wants to inspect each lookalike
    independently.
    """
    return f"cand_pw_{fresh_id}__{failed_id}"


def persist_warning(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    warning: PredictiveWarning,
    source_episode_id: str,
) -> str:
    """Write the warning as a ``memory_candidate(kind=predictive_warning)``.

    ``source_episode_id`` is required (FK constraint on
    ``memory_candidates.source_episode_id``). The caller is responsible
    for choosing an episode to attribute the warning to — typically
    the most recent episode in the workspace.

    Returns the candidate_id on insert, "" when:
      * the source episode id is empty (FK skip);
      * the conflicting row is already accepted/rejected (the
        ``WHERE status = 'new'`` clause makes the UPDATE a no-op).

    ``sqlite3.IntegrityError`` (FK violation) returns ""; other
    ``sqlite3.Error`` instances propagate so the caller can log them
    (same contract as ``persist_proposal``).
    """
    if not source_episode_id:
        return ""
    # Final-audit H3: pick the live table at runtime so canonical-only
    # deploys (``candidates`` instead of ``memory_candidates``) get
    # successful writes instead of silent failure.
    table = resolve_candidates_table(conn)
    if table is None:
        return ""
    candidate_id = candidate_id_for(warning.fresh_decision_id, warning.failed_decision_id)
    now = iso_now()
    try:
        cursor = conn.execute(
            f"""
            INSERT INTO {table} (
                id, workspace_id, kind, subject, predicate, object, evidence,
                confidence, importance, trust_level, temporal_json,
                write_targets_json, metadata_json, source_episode_id,
                status, created_at, updated_at
            ) VALUES (?, ?, 'predictive_warning', ?, 'lookalike_of', ?, ?,
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
                warning.fresh_decision_id,
                warning.failed_decision_id,
                warning.reason_text,
                warning.similarity,
                json.dumps({"source": "predictive_failure_mvp"}, sort_keys=True),
                source_episode_id,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        return ""
    if cursor.rowcount == 0:
        return ""
    return candidate_id
