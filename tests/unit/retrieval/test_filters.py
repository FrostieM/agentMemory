from __future__ import annotations

from agent_memory_lite.models.retrieval import ScoredHit
from agent_memory_lite.retrieval.filters import filter_active


def test_phase2_filter_is_passthrough() -> None:
    hits = [
        ScoredHit(id="a", workspace_id="default", text="x", score=0.5),
        ScoredHit(id="b", workspace_id="default", text="y", score=0.4),
    ]
    assert filter_active(hits) == hits
    assert filter_active(hits, historical=True) == hits
