from __future__ import annotations

from agent_memory_lite.extraction.trust_gate import passes_trust_gate
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel


def _candidate(kind: MemoryCandidateKind, trust: TrustLevel) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        subject="x",
        predicate="y",
        evidence="z",
        confidence=0.9,
        importance=0.8,
        trust_level=trust,
        temporal=TemporalSpan(
            observed_at="2026-04-26T00:00:00Z",
            valid_from="2026-04-26T00:00:00Z",
        ),
        source_episode_id="ep_x",
    )


def test_untrusted_doc_blocks_constraint() -> None:
    cand = _candidate(MemoryCandidateKind.CONSTRAINT, TrustLevel.UNTRUSTED_DOC)
    assert not passes_trust_gate(cand)


def test_untrusted_doc_blocks_procedural_rule() -> None:
    cand = _candidate(MemoryCandidateKind.PROCEDURAL_RULE, TrustLevel.UNTRUSTED_DOC)
    assert not passes_trust_gate(cand)


def test_untrusted_doc_allows_project_fact() -> None:
    cand = _candidate(MemoryCandidateKind.PROJECT_FACT, TrustLevel.UNTRUSTED_DOC)
    assert passes_trust_gate(cand)


def test_user_asserted_constraint_passes() -> None:
    cand = _candidate(MemoryCandidateKind.CONSTRAINT, TrustLevel.USER_ASSERTED)
    assert passes_trust_gate(cand)


def test_agent_observed_constraint_blocked() -> None:
    cand = _candidate(MemoryCandidateKind.CONSTRAINT, TrustLevel.AGENT_OBSERVED)
    assert not passes_trust_gate(cand)
