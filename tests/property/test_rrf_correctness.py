from __future__ import annotations

from itertools import pairwise

from hypothesis import given, settings
from hypothesis import strategies as st

from agent_memory_lite.models.retrieval import RetrievalCandidate
from agent_memory_lite.retrieval.fusion_rrf import reciprocal_rank_fusion


def _candidates(ids: list[str], source: str = "fts") -> list[RetrievalCandidate]:
    return [RetrievalCandidate(id=i, workspace_id="default", source=source, text=i) for i in ids]


_id_lists = st.lists(
    st.text(alphabet="abcdefghij", min_size=1, max_size=4),
    min_size=0,
    max_size=12,
    unique=True,
)


@given(ids=_id_lists)
@settings(max_examples=80, deadline=None)
def test_single_list_preserves_order(ids: list[str]) -> None:
    if not ids:
        return
    result = reciprocal_rank_fusion([_candidates(ids)])
    assert [r[0].id for r in result] == ids


@given(ids=_id_lists)
@settings(max_examples=80, deadline=None)
def test_scores_decrease_with_rank(ids: list[str]) -> None:
    if len(ids) < 2:
        return
    result = reciprocal_rank_fusion([_candidates(ids)])
    for prev, current in pairwise(result):
        assert prev[1] >= current[1]


@given(a=_id_lists, b=_id_lists)
@settings(max_examples=60, deadline=None)
def test_intersection_outranks_unique_ids(a: list[str], b: list[str]) -> None:
    shared = set(a) & set(b)
    if not shared:
        return
    fused = reciprocal_rank_fusion([_candidates(a, "fts"), _candidates(b, "vector")])
    fused_ids = [r[0].id for r in fused]
    fused_scores = {r[0].id: r[1] for r in fused}
    only_a = set(a) - set(b)
    if not only_a:
        return
    best_shared = max(shared, key=lambda i: fused_scores[i])
    worst_unique = min(only_a, key=lambda i: fused_scores[i])
    # The best dual-source hit lands no later than the worst single-source hit.
    assert fused_ids.index(best_shared) <= fused_ids.index(worst_unique)
