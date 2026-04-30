from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_memory_lite.models.retrieval import ScoredHit
from agent_memory_lite.retrieval.context_builder import filter_context_hits
from agent_memory_lite.utils.time import reset_now_provider, set_now_provider


def _hit(
    *,
    hit_id: str,
    score: float,
    sources: list[str] | None = None,
    fts_rank: int | None = None,
    created_at: str | None = None,
    text: str = "usable text",
) -> ScoredHit:
    metadata: dict[str, object] = {}
    if fts_rank is not None:
        metadata["fts_rank"] = fts_rank
    if created_at is not None:
        metadata["created_at"] = created_at
    return ScoredHit(
        id=hit_id,
        workspace_id="default",
        text=text,
        score=score,
        sources=sources or ["fts"],
        metadata=metadata,
    )


def test_filter_context_hits_suppresses_stale_low_score_noise() -> None:
    current = datetime(2026, 4, 30, tzinfo=UTC)
    set_now_provider(lambda: current)
    try:
        old = (current - timedelta(days=30)).isoformat()
        recent = (current - timedelta(days=1)).isoformat()
        hits = [
            _hit(hit_id="exact_old", score=0.1, fts_rank=0, created_at=old),
            _hit(hit_id="stale_low", score=0.3, fts_rank=8, created_at=old),
            _hit(hit_id="recent_vector", score=0.26, sources=["vector"], created_at=recent),
            _hit(hit_id="mojibake", score=0.9, text="bad \ufffd\ufffd\ufffd text"),
        ]

        kept = filter_context_hits(hits, historical=False)

        assert [hit.id for hit in kept] == ["exact_old", "recent_vector"]
    finally:
        reset_now_provider()


def test_filter_context_hits_keeps_all_for_historical_queries() -> None:
    stale = _hit(
        hit_id="stale_low", score=0.01, fts_rank=10, created_at="2000-01-01T00:00:00+00:00"
    )

    assert filter_context_hits([stale], historical=True) == [stale]
