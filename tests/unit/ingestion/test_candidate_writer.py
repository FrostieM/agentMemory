from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.candidate_writer import (
    promote_memory_candidate,
    write_memory_candidate,
)
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import (
    MemoryCandidateKind,
    MemoryCandidateStatus,
    TrustLevel,
)


def _candidate(source_episode_id: str, observed_at: str) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryCandidateKind.DECISION,
        subject="Keep extracted decisions reviewable",
        predicate="declared",
        evidence="Decision: Keep extracted decisions reviewable",
        confidence=0.9,
        importance=0.8,
        trust_level=TrustLevel.VERIFIED_BY_TOOL,
        temporal=TemporalSpan(observed_at=observed_at, valid_from=observed_at),
        source_episode_id=source_episode_id,
        write_targets=["decision"],
    )


def test_promote_candidate_writes_decision(applied_conn: sqlite3.Connection) -> None:
    episode = applied_conn.execute(
        """
        INSERT INTO episodes (
            id, workspace_id, source_type, raw_text, trust_level,
            importance, confidence, created_at
        ) VALUES ('ep_test', 'project-a', 'agent_action', 'x', 'verified_by_tool', 0.7, 1.0, '2026-01-01T00:00:00Z')
        """
    )
    assert episode is not None

    candidate = write_memory_candidate(
        applied_conn,
        workspace_id="project-a",
        candidate=_candidate("ep_test", "2026-01-01T00:00:00Z"),
    )
    promoted = promote_memory_candidate(applied_conn, candidate_id=candidate.id)

    assert promoted.status == MemoryCandidateStatus.PROMOTED
    assert promoted.promoted_target_type == "decision"
    assert promoted.promoted_target_id is not None
    decision = applied_conn.execute(
        "SELECT workspace_id, source_episode_id FROM decisions WHERE id = ?",
        (promoted.promoted_target_id,),
    ).fetchone()
    assert decision["workspace_id"] == "project-a"
    assert decision["source_episode_id"] == "ep_test"
