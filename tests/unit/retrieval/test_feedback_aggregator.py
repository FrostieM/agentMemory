"""Unit tests for the feedback EWMA aggregator (pure-function side).

Database integration is exercised by the integration suite; here we pin the
math + invariants so future refactors do not silently change ranking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_memory_lite.retrieval.feedback_aggregator import (
    SELF_LOOP_SOURCE,
    FeedbackRow,
    compute_ewma,
)


def _row(
    *,
    source_id: str = "ch_a",
    usefulness: float = 1.0,
    days_ago: float = 0.0,
    source: str = "agent_observed",
    reference: datetime | None = None,
) -> FeedbackRow:
    ref = reference if reference is not None else datetime.now(UTC)
    created = (ref - timedelta(days=days_ago)).isoformat()
    return FeedbackRow(
        source_id=source_id, usefulness=usefulness, created_at=created, source=source
    )


def test_empty_rows_returns_empty_result() -> None:
    assert compute_ewma([], half_life_days=14.0) == {}


def test_single_positive_row_yields_positive_one() -> None:
    out = compute_ewma([_row(usefulness=1.0)], half_life_days=14.0)
    assert pytest.approx(out["ch_a"].ewma, rel=1e-6) == 1.0
    assert out["ch_a"].sample_count == 1


def test_single_negative_row_yields_negative_one() -> None:
    out = compute_ewma([_row(usefulness=-1.0)], half_life_days=14.0)
    assert pytest.approx(out["ch_a"].ewma, rel=1e-6) == -1.0


def test_recent_positive_outweighs_older_negative() -> None:
    ref = datetime.now(UTC)
    rows = [
        _row(usefulness=-1.0, days_ago=30, reference=ref),
        _row(usefulness=1.0, days_ago=0, reference=ref),
    ]
    out = compute_ewma(rows, half_life_days=7.0, reference=ref)
    # Recent +1 has weight 1.0; -1 thirty days ago has weight 0.5^(30/7) ≈ 0.05.
    # Weighted mean is dominated by the positive recent row.
    assert out["ch_a"].ewma > 0.7


def test_result_is_bounded_in_minus_one_to_plus_one() -> None:
    ref = datetime.now(UTC)
    rows = [_row(usefulness=1.0, days_ago=i, reference=ref) for i in range(50)]
    out = compute_ewma(rows, half_life_days=14.0, reference=ref)
    assert -1.0 <= out["ch_a"].ewma <= 1.0


def test_self_loop_excluded_by_default() -> None:
    rows = [
        _row(usefulness=1.0, source=SELF_LOOP_SOURCE),
        _row(usefulness=-1.0, source="agent_observed"),
    ]
    out = compute_ewma(rows, half_life_days=14.0)
    # Self-loop row dropped, only -1 remains.
    assert pytest.approx(out["ch_a"].ewma, rel=1e-6) == -1.0
    assert out["ch_a"].sample_count == 1


def test_self_loop_included_when_filter_off() -> None:
    rows = [
        _row(usefulness=1.0, source=SELF_LOOP_SOURCE),
        _row(usefulness=-1.0, source="agent_observed"),
    ]
    out = compute_ewma(rows, half_life_days=14.0, exclude_self_loop=False)
    assert out["ch_a"].sample_count == 2


def test_per_day_cap_limits_inflation() -> None:
    ref = datetime.now(UTC)
    # Twenty +1 votes today, all from the same source — should be capped.
    rows = [_row(usefulness=1.0, days_ago=0.001 * i, reference=ref) for i in range(20)]
    out = compute_ewma(rows, half_life_days=14.0, reference=ref, max_per_day_per_source=5)
    assert out["ch_a"].sample_count == 5


def test_invalid_created_at_skipped() -> None:
    rows = [
        FeedbackRow(source_id="ch_a", usefulness=1.0, created_at="not-a-date", source="x"),
        _row(usefulness=-1.0),
    ]
    out = compute_ewma(rows, half_life_days=14.0)
    assert out["ch_a"].sample_count == 1
    assert pytest.approx(out["ch_a"].ewma, rel=1e-6) == -1.0


def test_multiple_source_ids_aggregated_independently() -> None:
    rows = [
        _row(source_id="ch_a", usefulness=1.0),
        _row(source_id="ch_b", usefulness=-1.0),
    ]
    out = compute_ewma(rows, half_life_days=14.0)
    assert pytest.approx(out["ch_a"].ewma, rel=1e-6) == 1.0
    assert pytest.approx(out["ch_b"].ewma, rel=1e-6) == -1.0
