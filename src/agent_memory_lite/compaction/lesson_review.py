"""Persist lesson proposals as canonical review candidates."""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.compaction.lesson_proposal import LessonProposal
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import MemoryCandidateKind, MemoryCandidateStatus, TrustLevel
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.candidates_repo import insert_candidate_row
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_lesson_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    proposals: list[LessonProposal],
    discarded_count: int = 0,
) -> list[str]:
    """Insert each proposal as a reviewable candidate. Returns new ids."""
    if not proposals and discarded_count <= 0:
        return []
    now_iso = iso_now()
    candidate_ids: list[str] = []
    for proposal in proposals:
        source_episode_ids = list(proposal.source_episode_ids)
        candidate_id = new_id(IdKind.MEMORY_CANDIDATE)
        candidate = MemoryCandidate(
            kind=MemoryCandidateKind.INSIGHT,
            subject=proposal.summary[:200],
            predicate="should_promote_to_insight",
            object=proposal.proposed_action,
            evidence=proposal.summary,
            confidence=proposal.confidence,
            importance=proposal.confidence,
            trust_level=TrustLevel.AGENT_INFERRED,
            temporal=TemporalSpan(observed_at=now_iso, valid_from=now_iso),
            write_targets=["insight"],
            source_episode_id=None,
            metadata={
                "source": "lesson_review",
                "insight_type": proposal.insight_type,
                "proposed_action": proposal.proposed_action,
                "source_episode_ids": source_episode_ids,
                "source_episode_ids_json": json.dumps(source_episode_ids),
            },
        )
        insert_candidate_row(
            conn,
            candidate_id=candidate_id,
            workspace_id=workspace_id,
            candidate=candidate,
            status=MemoryCandidateStatus.NEW,
            created_at=now_iso,
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
