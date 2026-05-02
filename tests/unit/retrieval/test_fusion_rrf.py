from __future__ import annotations

import pytest

from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.retrieval.fusion_rrf import reciprocal_rank_fusion


def _candidate(
    rid: str,
    source: str = "fts",
    *,
    metadata: dict[str, object] | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        id=rid,
        workspace_id="default",
        source=source,
        text=rid,
        metadata=metadata or {},
    )


def test_empty_input_returns_empty() -> None:
    assert reciprocal_rank_fusion([]) == []


def test_single_list_preserves_order() -> None:
    ranking = [_candidate("a"), _candidate("b"), _candidate("c")]
    result = reciprocal_rank_fusion([ranking])
    assert [r[0].id for r in result] == ["a", "b", "c"]


def test_overlapping_lists_boost_shared_ids() -> None:
    fts = [_candidate("a", "fts"), _candidate("b", "fts")]
    vec = [_candidate("b", "vector"), _candidate("c", "vector")]
    result = reciprocal_rank_fusion([fts, vec])
    top = result[0]
    assert top[0].id == "b"
    assert set(top[2]) == {"fts", "vector"}


def test_overlapping_lists_merge_source_metadata() -> None:
    fts = [_candidate("a", "fts", metadata={"fts_rank": 0})]
    vec = [_candidate("a", "vector", metadata={"vector_score": 0.75})]

    result = reciprocal_rank_fusion([fts, vec])

    assert result[0][0].metadata["fts_rank"] == 0
    assert result[0][0].metadata["vector_score"] == 0.75


def test_non_positive_k_rejected() -> None:
    with pytest.raises(ValueError, match="k must be positive"):
        reciprocal_rank_fusion([[_candidate("a")]], k=0)


def test_score_decreases_with_rank() -> None:
    ranking = [_candidate("a"), _candidate("b"), _candidate("c")]
    result = reciprocal_rank_fusion([ranking])
    assert result[0][1] > result[1][1] > result[2][1]
