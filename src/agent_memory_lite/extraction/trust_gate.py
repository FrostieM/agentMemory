"""Trust gate.

Untrusted document content (project files, third-party docs) must never be
promoted to core memory or procedural rules — those are operating instructions
the agent will follow. Only user-asserted, verified-by-tool, or explicit
decisions can be promoted there.
"""

from __future__ import annotations

from agent_memory_lite.extraction.thresholds import CORE_PROMOTION_TRUST
from agent_memory_lite.models.candidates import MemoryCandidate
from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel

# v3.5 sector-3 audit-followup: expanded list of kinds that an
# UNTRUSTED_DOC candidate must NOT promote into. Previously only
# PROCEDURAL_RULE / CONSTRAINT were guarded — DECISION / CORRECTION /
# BUG / FIX could land as candidates from doc-sourced episodes, then
# the operator could promote them into behaviors / decisions / core.
# All six kinds carry execution-shaping semantic weight, so they all
# need the higher trust floor.
PROMOTABLE_KINDS: frozenset[MemoryCandidateKind] = frozenset(
    {
        MemoryCandidateKind.PROCEDURAL_RULE,
        MemoryCandidateKind.CONSTRAINT,
        MemoryCandidateKind.DECISION,
        MemoryCandidateKind.CORRECTION,
        MemoryCandidateKind.BUG,
        MemoryCandidateKind.FIX,
    }
)


def passes_trust_gate(candidate: MemoryCandidate) -> bool:
    if candidate.trust_level == TrustLevel.UNTRUSTED_DOC:
        return candidate.kind not in PROMOTABLE_KINDS
    if candidate.kind in PROMOTABLE_KINDS:
        return candidate.trust_level in CORE_PROMOTION_TRUST
    return True
