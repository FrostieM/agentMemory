"""Confidence + importance thresholds per memory candidate kind."""

from __future__ import annotations

from agent_memory_lite.models.candidates import MemoryCandidate
from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel

KIND_THRESHOLDS: dict[MemoryCandidateKind, tuple[float, float]] = {
    MemoryCandidateKind.CONSTRAINT: (0.90, 0.85),
    MemoryCandidateKind.PROCEDURAL_RULE: (0.85, 0.75),
    MemoryCandidateKind.DECISION: (0.85, 0.75),
    MemoryCandidateKind.PROJECT_FACT: (0.70, 0.50),
    MemoryCandidateKind.TASK_STATE: (0.70, 0.50),
    MemoryCandidateKind.RELATIONSHIP: (0.70, 0.50),
    MemoryCandidateKind.CORRECTION: (0.85, 0.70),
    MemoryCandidateKind.BUG: (0.70, 0.50),
    MemoryCandidateKind.FIX: (0.70, 0.50),
}

CORE_PROMOTION_TRUST: frozenset[TrustLevel] = frozenset(
    {
        TrustLevel.USER_ASSERTED,
        TrustLevel.EXPLICIT_DECISION,
        TrustLevel.VERIFIED_BY_TOOL,
    }
)


def meets_thresholds(candidate: MemoryCandidate) -> bool:
    confidence_min, importance_min = KIND_THRESHOLDS.get(candidate.kind, (0.70, 0.50))
    return candidate.confidence >= confidence_min and candidate.importance >= importance_min
