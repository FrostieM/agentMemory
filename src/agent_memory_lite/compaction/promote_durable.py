"""Promote durable, high-trust candidates into core memory.

Phase 6 ships a deterministic version: the caller passes in candidates (e.g.
from a recent extraction pass). For each candidate that passes the trust gate
+ thresholds, write a core_memory row keyed on `subject` (lower-cased).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from agent_memory_lite.extraction.thresholds import meets_thresholds
from agent_memory_lite.extraction.trust_gate import passes_trust_gate
from agent_memory_lite.ingestion.core_memory_writer import write_core_memory
from agent_memory_lite.models.candidates import MemoryCandidate
from agent_memory_lite.models.core_memory import CoreMemoryIn
from agent_memory_lite.models.enums import MemoryCandidateKind


@dataclass(frozen=True, slots=True)
class PromotionStats:
    promoted: int
    skipped: int


_PROMOTABLE_KINDS = {
    MemoryCandidateKind.CONSTRAINT,
    MemoryCandidateKind.PROCEDURAL_RULE,
}


def promote_durable_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    candidates: Iterable[MemoryCandidate],
) -> PromotionStats:
    promoted = 0
    skipped = 0
    for candidate in candidates:
        if candidate.kind not in _PROMOTABLE_KINDS:
            skipped += 1
            continue
        if not passes_trust_gate(candidate):
            skipped += 1
            continue
        if not meets_thresholds(candidate):
            skipped += 1
            continue
        key = candidate.subject.strip().lower()
        if not key:
            skipped += 1
            continue
        write_core_memory(
            conn,
            CoreMemoryIn(
                workspace_id=workspace_id,
                key=key,
                value=candidate.evidence or candidate.subject,
                source_episode_id=candidate.source_episode_id,
                confidence=candidate.confidence,
                importance=candidate.importance,
            ),
        )
        promoted += 1
    return PromotionStats(promoted=promoted, skipped=skipped)
