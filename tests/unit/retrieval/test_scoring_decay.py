"""Confidence decay multiplies chunk scores by an age-based factor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.models.retrieval import ScoredHit
from agent_memory_lite.retrieval.scoring_decay import apply_confidence_decay


def _hit(score: float, *, days_ago: float, hit_id: str = "chk") -> ScoredHit:
    created = datetime.now(UTC) - timedelta(days=days_ago)
    return ScoredHit(
        id=hit_id,
        workspace_id="qa",
        text=f"hit {hit_id}",
        path="",
        summary=None,
        score=score,
        sources=["fts"],
        metadata={"created_at": created.isoformat()},
    )


def test_decay_disabled_returns_input_unchanged() -> None:
    cfg = Settings(MEMORY_CONFIDENCE_DECAY_ENABLED="0")
    hit = _hit(0.5, days_ago=30)
    out = apply_confidence_decay([hit], settings=cfg)
    assert out == [hit]


def test_decay_applies_half_life() -> None:
    cfg = Settings(
        MEMORY_CONFIDENCE_DECAY_ENABLED="1",
        MEMORY_CONFIDENCE_DECAY_HALF_LIFE_DAYS="10",
    )
    fresh = _hit(1.0, days_ago=0, hit_id="fresh")
    one_half = _hit(1.0, days_ago=10, hit_id="one_half")
    two_half = _hit(1.0, days_ago=20, hit_id="two_half")

    [fresh_out, one_out, two_out] = apply_confidence_decay(
        [fresh, one_half, two_half], settings=cfg
    )
    # Output is sorted by score desc, so freshest leads.
    assert fresh_out.id == "fresh"
    assert fresh_out.score == pytest.approx(1.0, rel=1e-3)
    assert one_out.id == "one_half"
    assert one_out.score == pytest.approx(0.5, rel=1e-3)
    assert two_out.id == "two_half"
    assert two_out.score == pytest.approx(0.25, rel=1e-3)
    assert fresh_out.metadata["confidence_decay_factor"] == pytest.approx(1.0, abs=1e-2)


def test_decay_skips_hits_without_created_at() -> None:
    cfg = Settings(MEMORY_CONFIDENCE_DECAY_ENABLED="1")
    hit = ScoredHit(
        id="no-time",
        workspace_id="qa",
        text="x",
        path="",
        summary=None,
        score=0.7,
        sources=["fts"],
        metadata={},
    )
    out = apply_confidence_decay([hit], settings=cfg)
    assert out[0].score == 0.7  # untouched
