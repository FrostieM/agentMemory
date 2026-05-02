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
from agent_memory_lite.ingestion.core_memory_writer import write_core_memory
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.procedural_writer import write_procedural_rule
from agent_memory_lite.models.candidates import StoredMemoryCandidate
from agent_memory_lite.models.core_memory import CoreMemoryIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import MemoryCandidateKind
from agent_memory_lite.models.procedural import ProceduralRuleIn


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


def _promote_procedural_rule(
    conn: sqlite3.Connection, candidate: StoredMemoryCandidate
) -> tuple[str, str]:
    rule = write_procedural_rule(
        conn,
        ProceduralRuleIn(
            workspace_id=candidate.workspace_id,
            rule_text=candidate.evidence or candidate.subject,
            source_episode_id=candidate.source_episode_id,
            confidence=candidate.confidence,
            importance=candidate.importance,
        ),
    )
    return "procedural_rule", rule.id


def _promote_constraint(
    conn: sqlite3.Connection, candidate: StoredMemoryCandidate
) -> tuple[str, str]:
    core = write_core_memory(
        conn,
        CoreMemoryIn(
            workspace_id=candidate.workspace_id,
            key=candidate.subject.strip().lower()[:80] or "candidate.constraint",
            value=candidate.evidence or candidate.subject,
            source_episode_id=candidate.source_episode_id,
            confidence=candidate.confidence,
            importance=candidate.importance,
        ),
    )
    return "core_memory", core.id


_PROMOTERS = {
    MemoryCandidateKind.DECISION: _promote_decision,
    MemoryCandidateKind.PROCEDURAL_RULE: _promote_procedural_rule,
    MemoryCandidateKind.CONSTRAINT: _promote_constraint,
}


def promote_to_target(
    conn: sqlite3.Connection, candidate: StoredMemoryCandidate
) -> tuple[str, str]:
    """Run the kind-specific promoter; raise when the kind isn't promotable."""
    promoter = _PROMOTERS.get(candidate.kind)
    if promoter is None:
        raise ValidationError(f"candidate kind {candidate.kind.value!r} is not promotable")
    return promoter(conn, candidate)
