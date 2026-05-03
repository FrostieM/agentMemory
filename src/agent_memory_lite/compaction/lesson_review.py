"""Persist lesson proposals as ``insight_candidates`` rows.

Trust-gate invariant: this module ONLY writes to ``insight_candidates``.
It must NEVER INSERT into ``research_insights`` directly. The
no-auto-promote test asserts that. Promotion happens only via the
explicit /memory/insight_candidates/{id}/accept endpoint.

Audit volume policy: a single ``compaction.lesson_proposed`` audit row
covers the whole batch (per plan blind spot #5). ``compaction.lesson_
rejected_low_support`` is recorded as a count delta when proposals were
generated but failed validation (the parser already filters them; the
audit records the size of the discard).
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.compaction.lesson_proposal import LessonProposal
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_lesson_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    proposals: list[LessonProposal],
    discarded_count: int = 0,
) -> list[str]:
    """Insert each proposal as a pending row. Returns the new candidate ids.

    Empty input → empty result, no audit row written.
    """
    if not proposals and discarded_count <= 0:
        return []
    now_iso = iso_now()
    candidate_ids: list[str] = []
    for proposal in proposals:
        candidate_id = new_id(IdKind.INSIGHT_CANDIDATE)
        conn.execute(
            """
            INSERT INTO insight_candidates
            (id, workspace_id, insight_type, summary, proposed_action,
             target_type, target_id, source_episode_ids_json, confidence,
             status, tags_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, 'pending', '[]', ?, ?)
            """,
            (
                candidate_id,
                workspace_id,
                proposal.insight_type,
                proposal.summary,
                proposal.proposed_action,
                json.dumps(list(proposal.source_episode_ids)),
                proposal.confidence,
                now_iso,
                now_iso,
            ),
        )
        candidate_ids.append(candidate_id)
    if proposals:
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="compaction.lesson_proposed",
            target_type="workspace",
            target_id=workspace_id,
            after={"count": len(proposals), "ids": candidate_ids, "at": now_iso},
        )
    if discarded_count > 0:
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="compaction.lesson_rejected_low_support",
            target_type="workspace",
            target_id=workspace_id,
            after={"discarded": discarded_count, "at": now_iso},
        )
    return candidate_ids
