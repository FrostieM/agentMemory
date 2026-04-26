from __future__ import annotations

from agent_memory_lite.extraction.thresholds import KIND_THRESHOLDS, meets_thresholds
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel


def _candidate(
    kind: MemoryCandidateKind,
    *,
    confidence: float,
    importance: float,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        subject="x",
        predicate="y",
        evidence="evidence",
        confidence=confidence,
        importance=importance,
        trust_level=TrustLevel.AGENT_OBSERVED,
        temporal=TemporalSpan(
            observed_at="2026-04-26T00:00:00Z",
            valid_from="2026-04-26T00:00:00Z",
        ),
        source_episode_id="ep_x",
    )


def test_kind_thresholds_listed() -> None:
    for kind in (
        MemoryCandidateKind.CONSTRAINT,
        MemoryCandidateKind.PROCEDURAL_RULE,
        MemoryCandidateKind.DECISION,
    ):
        assert kind in KIND_THRESHOLDS


def test_high_confidence_constraint_passes() -> None:
    cand = _candidate(MemoryCandidateKind.CONSTRAINT, confidence=0.95, importance=0.9)
    assert meets_thresholds(cand)


def test_low_confidence_constraint_fails() -> None:
    cand = _candidate(MemoryCandidateKind.CONSTRAINT, confidence=0.5, importance=0.9)
    assert not meets_thresholds(cand)


def test_low_importance_decision_fails() -> None:
    cand = _candidate(MemoryCandidateKind.DECISION, confidence=0.95, importance=0.1)
    assert not meets_thresholds(cand)
