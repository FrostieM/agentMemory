"""Promotion targets for reviewable memory candidates.

Split out of ``candidate_writer.py`` so the dispatch table stays
readable and the writer module stays under the SLOC ceiling. Each
candidate kind has a single ``_promote_*`` function that builds the
``*In`` payload, calls the corresponding writer, and returns the
``(target_type, target_id)`` pair the writer records.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.api.errors import ValidationError
from agent_memory_lite.extraction.trust_gate import passes_trust_gate
from agent_memory_lite.ingestion.behavior_writer import upsert_behavior_instruction
from agent_memory_lite.ingestion.candidate_behavior import behavior_payload_from_candidate
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.research_writer_insights import distill_insight
from agent_memory_lite.models.candidates import StoredMemoryCandidate
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import InsightStatus, InsightType, MemoryCandidateKind
from agent_memory_lite.models.research import ResearchInsightIn


def _promote_decision(
    conn: sqlite3.Connection, candidate: StoredMemoryCandidate
) -> tuple[str, str]:
    decision = write_decision(
        conn,
        DecisionIn(
            workspace_id=candidate.workspace_id,
            title=candidate.subject[:80] or "Promoted memory candidate",
            decision_text=candidate.evidence or candidate.subject,
            source_episode_id=candidate.source_episode_id,
            confidence=candidate.confidence,
            importance=candidate.importance,
        ),
    )
    return "decision", decision.id


def _promote_constraint(
    conn: sqlite3.Connection, candidate: StoredMemoryCandidate
) -> tuple[str, str]:
    behavior = upsert_behavior_instruction(
        conn, behavior_payload_from_candidate(candidate, workspace_id=candidate.workspace_id)
    )
    return "behavior", behavior.id


def _insight_type_from_candidate(candidate: StoredMemoryCandidate) -> InsightType:
    raw = str(candidate.metadata.get("insight_type") or "")
    try:
        return InsightType(raw)
    except ValueError:
        if raw in {"lesson_learned", "anti_pattern", "process_improvement"}:
            return InsightType.LESSON
        if raw == "hypothesis":
            return InsightType.THEORY_CANDIDATE
        return InsightType.OPEN_QUESTION


def _promote_insight(conn: sqlite3.Connection, candidate: StoredMemoryCandidate) -> tuple[str, str]:
    raw_episode_ids = candidate.metadata.get("source_episode_ids", [])
    source_episode_ids = (
        [str(item) for item in raw_episode_ids if isinstance(item, str)]
        if isinstance(raw_episode_ids, list)
        else []
    )
    insight = distill_insight(
        conn,
        ResearchInsightIn(
            workspace_id=candidate.workspace_id,
            insight_type=_insight_type_from_candidate(candidate),
            summary=candidate.evidence or candidate.subject,
            proposed_action=candidate.object,
            source_episode_ids=source_episode_ids,
            confidence=candidate.confidence,
            status=InsightStatus.NEW,
            tags=[],
        ),
    )
    return "insight", insight.id


_PROMOTERS = {
    MemoryCandidateKind.DECISION: _promote_decision,
    MemoryCandidateKind.INSIGHT: _promote_insight,
    MemoryCandidateKind.PROCEDURAL_RULE: _promote_constraint,
    MemoryCandidateKind.CONSTRAINT: _promote_constraint,
}


def promote_to_target(
    conn: sqlite3.Connection, candidate: StoredMemoryCandidate
) -> tuple[str, str]:
    """Run the kind-specific promoter; raise when the kind isn't promotable.

    Round-2 audit: the trust gate used to run ONCE, at candidate-write
    time inside ``auto_promote._filter``. Promotion is the actual
    privilege escalation from candidate into decision or behavior memory.
    The promotion path previously ran zero trust validation. A candidate whose
    ``trust_level`` was mutated after write (a direct DB edit, a future
    writer, or any path that skipped ``_filter``) could be promoted
    straight into execution-shaping memory. Re-check the gate here,
    against the STORED row, immediately before dispatch.

    Only the trust gate is re-checked, not ``meets_thresholds``: a
    low-confidence candidate that an operator explicitly chooses to
    promote is a deliberate human override; the trust gate is a hard
    security boundary (untrusted-doc content never becomes a rule —
    CLAUDE.md "Untrusted documents stay untrusted") and holds even
    against operator action.
    """
    if not passes_trust_gate(candidate):
        raise ValidationError(
            f"candidate {candidate.id} (kind={candidate.kind.value}, "
            f"trust={candidate.trust_level.value}) fails the trust gate: "
            "untrusted-document content cannot be promoted into "
            "execution-shaping memory (decision / behavior)."
        )
    promoter = _PROMOTERS.get(candidate.kind)
    if promoter is None:
        raise ValidationError(f"candidate kind {candidate.kind.value!r} is not promotable")
    return promoter(conn, candidate)
