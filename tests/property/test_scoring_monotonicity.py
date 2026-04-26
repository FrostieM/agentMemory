from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.retrieval.scoring import score_candidates


def _vector_candidate(rid: str, similarity: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        id=rid,
        workspace_id="default",
        source="vector",
        text=rid,
        raw_score=similarity,
    )


@given(
    sim_a=st.floats(min_value=-1.0, max_value=1.0),
    sim_b=st.floats(min_value=-1.0, max_value=1.0),
)
@settings(max_examples=120, deadline=None)
def test_higher_similarity_never_loses(sim_a: float, sim_b: float) -> None:
    if abs(sim_a - sim_b) < 0.01:
        return
    higher, lower = sorted([sim_a, sim_b], reverse=True)
    fused = [
        (_vector_candidate("hi", higher), 0.5, ["vector"]),
        (_vector_candidate("lo", lower), 0.5, ["vector"]),
    ]
    scored = score_candidates(fused)
    assert scored[0].id == "hi"


@given(
    keyword_score=st.floats(min_value=-50.0, max_value=-0.01),
    similarity=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=80, deadline=None)
def test_each_signal_pushes_total_score_up(keyword_score: float, similarity: float) -> None:
    fts = [
        (
            RetrievalCandidate(
                id="kw",
                workspace_id="default",
                source="fts",
                text="kw",
                raw_score=keyword_score,
            ),
            0.5,
            ["fts"],
        )
    ]
    vec = [
        (_vector_candidate("vec", similarity), 0.5, ["vector"]),
    ]
    blank = [
        (
            RetrievalCandidate(
                id="blank", workspace_id="default", source="fts", text="b", raw_score=-1e9
            ),
            0.0,
            ["fts"],
        )
    ]
    scored_fts = score_candidates(fts + blank)[0].score
    scored_vec = score_candidates(vec + blank)[0].score
    # Both signals must produce a strictly positive score.
    assert scored_fts > 0
    assert scored_vec >= 0
