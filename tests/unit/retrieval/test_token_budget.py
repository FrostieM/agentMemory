from __future__ import annotations

from agent_memory_lite.models.retrieval import ScoredHit
from agent_memory_lite.retrieval.token_budget import fit_within_budget


def _hit(rid: str, text: str, score: float = 0.5) -> ScoredHit:
    return ScoredHit(
        id=rid,
        workspace_id="default",
        text=text,
        score=score,
    )


def test_zero_budget_returns_empty() -> None:
    hits = [_hit("a", "long text" * 10)]
    assert fit_within_budget(hits, max_tokens=0) == []


def test_packs_only_fitting_hits() -> None:
    hits = [_hit("a", "x" * 80), _hit("b", "y" * 80), _hit("c", "z" * 1000)]
    result = fit_within_budget(hits, max_tokens=100)
    ids = [hit.id for hit in result]
    assert "a" in ids
    assert "c" not in ids


def test_preserves_input_order() -> None:
    hits = [_hit(f"x{i}", "short") for i in range(5)]
    result = fit_within_budget(hits, max_tokens=200)
    assert [hit.id for hit in result] == [hit.id for hit in hits]
