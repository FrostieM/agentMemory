from __future__ import annotations

from agent_memory_lite.extraction.heuristic_extractor import HeuristicExtractor
from agent_memory_lite.models.enums import EpisodeSource, MemoryCandidateKind, TrustLevel
from agent_memory_lite.models.episodes import Episode


def _episode(text: str, *, trust: TrustLevel = TrustLevel.AGENT_OBSERVED) -> Episode:
    return Episode(
        id="ep_x",
        workspace_id="default",
        session_id=None,
        task_id=None,
        source_type=EpisodeSource.AGENT_ACTION,
        raw_text=text,
        summary=None,
        trust_level=trust,
        importance=0.7,
        confidence=1.0,
        created_at="2026-04-26T00:00:00Z",
    )


def test_no_cues_yields_no_candidates() -> None:
    extractor = HeuristicExtractor()
    assert extractor.extract(_episode("plain progress note")) == []


def test_decision_line_picked_up() -> None:
    extractor = HeuristicExtractor()
    candidates = extractor.extract(_episode("Decision: ship Phase 3 with heuristic extraction."))
    assert any(
        c.kind == MemoryCandidateKind.DECISION and "Phase 3" in c.subject for c in candidates
    )


def test_rule_line_picked_up() -> None:
    extractor = HeuristicExtractor()
    candidates = extractor.extract(_episode("Rule: always redact secrets before storage."))
    assert any(c.kind == MemoryCandidateKind.PROCEDURAL_RULE for c in candidates)


def test_russian_cues_supported() -> None:
    extractor = HeuristicExtractor()
    candidates = extractor.extract(_episode("Решение: использовать SQLite + LanceDB."))
    assert candidates
    assert candidates[0].kind == MemoryCandidateKind.DECISION


def test_carry_episode_trust_level() -> None:
    extractor = HeuristicExtractor()
    candidates = extractor.extract(_episode("Decision: foo", trust=TrustLevel.USER_ASSERTED))
    assert candidates[0].trust_level == TrustLevel.USER_ASSERTED
