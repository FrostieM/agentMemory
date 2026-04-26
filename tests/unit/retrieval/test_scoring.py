from __future__ import annotations

from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.retrieval.scoring import score_candidates


def _candidate(rid: str, source: str, raw: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        id=rid,
        workspace_id="default",
        source=source,
        text=rid,
        raw_score=raw,
    )


def test_empty_input_yields_empty() -> None:
    assert score_candidates([]) == []


def test_higher_vector_similarity_wins() -> None:
    fused = [
        (_candidate("hi", "vector", 0.95), 0.5, ["vector"]),
        (_candidate("lo", "vector", 0.10), 0.5, ["vector"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "hi"


def test_higher_keyword_relevance_outranks_lower() -> None:
    fused = [
        (_candidate("hi", "fts", -2.0), 0.5, ["fts"]),
        (_candidate("lo", "fts", -8.0), 0.5, ["fts"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "hi"


def test_multi_source_outranks_single_when_signals_tied() -> None:
    fused = [
        (_candidate("multi", "fts", -2.0), 0.6, ["fts", "vector"]),
        (_candidate("solo", "fts", -2.0), 0.3, ["fts"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "multi"


def test_scored_hits_carry_sources() -> None:
    fused = [(_candidate("x", "vector", 0.5), 0.5, ["vector"])]
    scored = score_candidates(fused)
    assert scored[0].sources == ["vector"]
