from __future__ import annotations

from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.retrieval.scoring import score_candidates


def _candidate(
    rid: str,
    source: str,
    raw: float,
    *,
    metadata: dict[str, object] | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        id=rid,
        workspace_id="default",
        source=source,
        text=rid,
        raw_score=raw,
        metadata=metadata or {},
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
        (_candidate("hi", "fts", -20.0, metadata={"fts_rank": 0}), 0.5, ["fts"]),
        (_candidate("lo", "fts", -2.0, metadata={"fts_rank": 3}), 0.5, ["fts"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "hi"


def test_exact_fts_hit_outranks_weak_vector_only_hit() -> None:
    fused = [
        (_candidate("exact", "fts", -50.0, metadata={"fts_rank": 0}), 0.2, ["fts"]),
        (_candidate("semantic", "vector", 0.25), 0.8, ["vector"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "exact"


def test_multi_source_outranks_single_when_signals_tied() -> None:
    fused = [
        (
            _candidate(
                "multi",
                "fts",
                -2.0,
                metadata={"fts_rank": 0, "vector_score": 0.4},
            ),
            0.6,
            ["fts", "vector"],
        ),
        (_candidate("solo", "fts", -2.0, metadata={"fts_rank": 0}), 0.3, ["fts"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "multi"


def test_scored_hits_carry_sources() -> None:
    fused = [(_candidate("x", "vector", 0.5), 0.5, ["vector"])]
    scored = score_candidates(fused)
    assert scored[0].sources == ["vector"]


def test_higher_importance_outranks_lower_when_other_signals_tied() -> None:
    fused = [
        (
            _candidate(
                "high",
                "fts",
                -2.0,
                metadata={"fts_rank": 0, "importance": 0.95},
            ),
            0.5,
            ["fts"],
        ),
        (
            _candidate(
                "low",
                "fts",
                -2.0,
                metadata={"fts_rank": 0, "importance": 0.10},
            ),
            0.5,
            ["fts"],
        ),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "high"


def test_positive_feedback_ewma_lifts_candidate() -> None:
    fused = [
        (
            _candidate(
                "boosted",
                "fts",
                -2.0,
                metadata={"fts_rank": 0, "feedback_ewma": 0.9},
            ),
            0.5,
            ["fts"],
        ),
        (_candidate("plain", "fts", -2.0, metadata={"fts_rank": 0}), 0.5, ["fts"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "boosted"


def test_negative_feedback_ewma_demotes_candidate() -> None:
    fused = [
        (
            _candidate(
                "noisy",
                "fts",
                -2.0,
                metadata={"fts_rank": 0, "feedback_ewma": -0.9},
            ),
            0.5,
            ["fts"],
        ),
        (_candidate("plain", "fts", -2.0, metadata={"fts_rank": 0}), 0.5, ["fts"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "plain"


def test_missing_metadata_terms_preserve_legacy_score() -> None:
    """Parity: candidates without importance/recency/feedback metadata produce
    the same ranking as before v1.4 — the new terms default to 0.0."""
    fused = [
        (_candidate("a", "vector", 0.95), 0.5, ["vector"]),
        (_candidate("b", "vector", 0.10), 0.5, ["vector"]),
    ]
    scored = score_candidates(fused)
    assert [hit.id for hit in scored] == ["a", "b"]
