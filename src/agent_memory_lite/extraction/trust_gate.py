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

PROMOTABLE_KINDS: frozenset[MemoryCandidateKind] = frozenset(
    {MemoryCandidateKind.PROCEDURAL_RULE, MemoryCandidateKind.CONSTRAINT}
)


def passes_trust_gate(candidate: MemoryCandidate) -> bool:
    if candidate.trust_level == TrustLevel.UNTRUSTED_DOC:
        return candidate.kind not in PROMOTABLE_KINDS
    if candidate.kind in PROMOTABLE_KINDS:
        return candidate.trust_level in CORE_PROMOTION_TRUST
    return True
