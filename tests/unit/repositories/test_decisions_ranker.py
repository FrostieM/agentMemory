"""Lock in the v3.5 decisions ranker semantics.

MemBench MVP measured the ranker against itself on 2026-05-20: the
baseline reported MRR 0.3611 / recall@5 100% — meaning the matching
decision was always in the top-10 but rarely at rank 1. The dominant
failure mode was pinned-first ordering: two pinned decisions always
sat at ranks 1-2 regardless of the query, so any non-pinned
title-verbatim query was capped at rank ≥ 3 → MRR ≤ 1/3.

The v3.5 ranker keeps pinned semantics for browse mode (no query)
but collapses pinned-first to a score boost when a query is present.
It also weights title tokens 3x body tokens and gives an
all-tokens-in-title bonus, so an exact title match dominates partial
sibling matches. Re-measured MemBench after the change: MRR 0.9889
(+0.6278). These tests fix the contract so a future tweak can't
regress that win silently.
"""

from __future__ import annotations

from agent_memory_lite.models.decisions import Decision
from agent_memory_lite.models.enums import DecisionStatus
from agent_memory_lite.repositories.decisions_search import filter_rank_limit


def _make_decision(
    *,
    decision_id: str,
    title: str,
    decision_text: str = "",
    rationale: str | None = None,
    pinned: bool = False,
    importance: float = 0.5,
    confidence: float = 0.5,
    outcome_score: float = 0.0,
) -> Decision:
    return Decision(
        id=decision_id,
        workspace_id="ws",
        title=title,
        decision_text=decision_text,
        rationale=rationale,
        status=DecisionStatus.ACTIVE,
        supersedes_decision_id=None,
        source_episode_id=None,
        importance=importance,
        confidence=confidence,
        pinned=pinned,
        outcome_score=outcome_score,
        valid_from="2026-05-20T00:00:00+00:00",
        valid_to=None,
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
        references=[],
    )


def test_browse_mode_keeps_pinned_first() -> None:
    """No query → pinned decisions still come first. The
    list-decisions endpoint must keep its "favorites at top" behavior
    for operator browsing — that's the whole point of /memory/pin."""
    pinned = _make_decision(decision_id="dec_pinned", title="Pinned A", pinned=True)
    normal = _make_decision(decision_id="dec_normal", title="Normal B", importance=0.9)
    ranked = filter_rank_limit([normal, pinned], query=None, limit=None)
    assert ranked[0].id == "dec_pinned"
    assert ranked[1].id == "dec_normal"


def test_query_mode_lets_exact_title_match_beat_pinned() -> None:
    """A non-pinned decision whose title MATCHES the query must beat
    pinned decisions whose titles don't. This was the v3.4 MemBench
    failure mode — pinned-first ordering capped MRR at ~1/3."""
    pinned_a = _make_decision(decision_id="dec_pin_a", title="Quarterly review", pinned=True)
    pinned_b = _make_decision(decision_id="dec_pin_b", title="Architecture invariants", pinned=True)
    target = _make_decision(decision_id="dec_target", title="MemBench MVP first number")
    ranked = filter_rank_limit(
        [pinned_a, pinned_b, target],
        query="MemBench MVP first number",
        limit=None,
    )
    assert ranked[0].id == "dec_target"


def test_pinned_breaks_ties_under_query() -> None:
    """When two decisions have identical content scores under a query,
    the pinned one wins. Pinned semantics still influences ranking —
    just doesn't dominate over an exact-title-match difference."""
    pinned = _make_decision(decision_id="dec_pin", title="Shared topic note", pinned=True)
    normal = _make_decision(decision_id="dec_norm", title="Shared topic note")
    ranked = filter_rank_limit([normal, pinned], query="Shared topic note", limit=None)
    assert ranked[0].id == "dec_pin"


def test_title_match_beats_body_match() -> None:
    """Title tokens carry 3x the weight of body tokens. A decision
    whose body mentions the query is less relevant than one whose
    title does."""
    title_match = _make_decision(
        decision_id="dec_title",
        title="Coerce enum on read path",
        decision_text="unrelated body",
    )
    body_match = _make_decision(
        decision_id="dec_body",
        title="Unrelated title",
        decision_text="Coerce enum on read path appears here once",
    )
    ranked = filter_rank_limit(
        [body_match, title_match],
        query="Coerce enum on read path",
        limit=None,
    )
    assert ranked[0].id == "dec_title"


def test_outcome_score_breaks_ties_between_equal_titles() -> None:
    """When two decisions have identical token scores, the one with
    a higher outcome_score (positive feedback / no supersede / etc.)
    wins. v3.0.0 outcome loop must influence ranking, not just be a
    UI badge."""
    good = _make_decision(
        decision_id="dec_good",
        title="Routing rule",
        outcome_score=0.8,
    )
    weak = _make_decision(
        decision_id="dec_weak",
        title="Routing rule",
        outcome_score=-0.5,
    )
    ranked = filter_rank_limit([weak, good], query="Routing rule", limit=None)
    assert ranked[0].id == "dec_good"


def test_limit_respected() -> None:
    """The ranker must respect ``limit`` — guards against a refactor
    that drops the trailing slice."""
    decisions = [_make_decision(decision_id=f"dec_{i}", title=f"Title {i}") for i in range(20)]
    ranked = filter_rank_limit(decisions, query=None, limit=5)
    assert len(ranked) == 5
