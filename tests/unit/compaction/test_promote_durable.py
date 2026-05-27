from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.compaction.promote_durable import promote_durable_candidates
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import (
    BehaviorInstructionKind,
    BehaviorInstructionPriority,
    EpisodeSource,
    MemoryCandidateKind,
    TrustLevel,
)
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.repositories.episodes_repo import insert_episode


@pytest.fixture
def source_episode_id(applied_conn: sqlite3.Connection) -> str:
    episode = insert_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="default",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="seed for promotion tests",
            trust_level=TrustLevel.USER_ASSERTED,
        ),
    )
    return episode.id


def _candidate(
    source_episode_id: str,
    *,
    kind: MemoryCandidateKind,
    trust: TrustLevel = TrustLevel.USER_ASSERTED,
    confidence: float = 0.95,
    importance: float = 0.9,
    subject: str = "memory must be local",
) -> MemoryCandidate:
    span = TemporalSpan(
        observed_at="2026-04-26T00:00:00Z",
        valid_from="2026-04-26T00:00:00Z",
    )
    return MemoryCandidate(
        kind=kind,
        subject=subject,
        predicate="declared",
        evidence="user said so",
        confidence=confidence,
        importance=importance,
        trust_level=trust,
        temporal=span,
        source_episode_id=source_episode_id,
    )


def test_user_asserted_constraint_promoted(
    applied_conn: sqlite3.Connection, source_episode_id: str
) -> None:
    stats = promote_durable_candidates(
        applied_conn,
        workspace_id="default",
        candidates=[_candidate(source_episode_id, kind=MemoryCandidateKind.CONSTRAINT)],
    )
    assert stats.promoted == 1
    assert stats.skipped == 0
    row = applied_conn.execute(
        "SELECT kind, priority, rule FROM behaviors WHERE workspace_id = ? AND active = 1",
        ("default",),
    ).fetchone()
    assert row is not None
    assert row["kind"] == BehaviorInstructionKind.OPERATING_RULE.value
    assert row["priority"] == BehaviorInstructionPriority.SYSTEM_BOUND.value
    assert row["rule"] == "user said so"


def test_untrusted_doc_skipped(applied_conn: sqlite3.Connection, source_episode_id: str) -> None:
    stats = promote_durable_candidates(
        applied_conn,
        workspace_id="default",
        candidates=[
            _candidate(
                source_episode_id,
                kind=MemoryCandidateKind.PROCEDURAL_RULE,
                trust=TrustLevel.UNTRUSTED_DOC,
            )
        ],
    )
    assert stats.promoted == 0
    assert stats.skipped == 1


def test_low_confidence_skipped(applied_conn: sqlite3.Connection, source_episode_id: str) -> None:
    stats = promote_durable_candidates(
        applied_conn,
        workspace_id="default",
        candidates=[
            _candidate(source_episode_id, kind=MemoryCandidateKind.CONSTRAINT, confidence=0.5)
        ],
    )
    assert stats.promoted == 0
    assert stats.skipped == 1


def test_non_promotable_kind_skipped(
    applied_conn: sqlite3.Connection, source_episode_id: str
) -> None:
    stats = promote_durable_candidates(
        applied_conn,
        workspace_id="default",
        candidates=[_candidate(source_episode_id, kind=MemoryCandidateKind.PROJECT_FACT)],
    )
    assert stats.promoted == 0
    assert stats.skipped == 1
