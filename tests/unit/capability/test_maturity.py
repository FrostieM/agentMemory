"""Pin the maturity curve so future tuning doesn't silently change rankings."""

from __future__ import annotations

import pytest

from agent_memory_lite.capability.maturity import (
    EVIDENCE_HALF_POINT,
    MaturityInputs,
    confidence_from_maturity,
    evidence_weight,
    staleness_factor,
    success_rate,
)


def _inputs(
    *,
    base: float = 0.7,
    usage: int = 0,
    successes: int = 0,
    failures: int = 0,
    age_days: float | None = None,
) -> MaturityInputs:
    return MaturityInputs(
        base=base,
        usage_count=usage,
        success_count=successes,
        failure_count=failures,
        age_days_since_last_use=age_days,
    )


def test_no_evidence_returns_base() -> None:
    out = confidence_from_maturity(_inputs(base=0.7))
    assert pytest.approx(out, rel=1e-6) == 0.7


def test_high_success_rate_with_strong_evidence_lifts_above_base() -> None:
    out = confidence_from_maturity(
        _inputs(base=0.5, usage=100, successes=95, failures=5, age_days=0.0)
    )
    assert out > 0.85


def test_high_failure_rate_with_strong_evidence_drops_below_base() -> None:
    out = confidence_from_maturity(
        _inputs(base=0.9, usage=100, successes=5, failures=95, age_days=0.0)
    )
    assert out < 0.2


def test_long_idle_period_drifts_back_toward_base() -> None:
    fresh = confidence_from_maturity(
        _inputs(base=0.5, usage=50, successes=50, failures=0, age_days=0.0)
    )
    stale = confidence_from_maturity(
        _inputs(base=0.5, usage=50, successes=50, failures=0, age_days=180.0)
    )
    # Both should be above base because evidence supports the capability,
    # but the stale one should be much closer to base.
    assert fresh > stale > 0.5
    assert (stale - 0.5) < 0.2 * (fresh - 0.5)


def test_result_is_bounded_in_zero_to_one() -> None:
    extreme = confidence_from_maturity(
        _inputs(base=0.99, usage=1000, successes=0, failures=1000, age_days=0.0)
    )
    assert 0.0 <= extreme <= 1.0


def test_success_rate_neutral_when_no_evidence() -> None:
    assert success_rate(0, 0) == 0.5


def test_success_rate_pure_failures() -> None:
    assert success_rate(0, 10) == 0.0


def test_evidence_weight_zero_at_no_usage() -> None:
    assert evidence_weight(0) == 0.0


def test_evidence_weight_at_half_point() -> None:
    assert pytest.approx(evidence_weight(int(EVIDENCE_HALF_POINT)), rel=1e-6) == 0.5


def test_evidence_weight_asymptotes_to_one() -> None:
    assert evidence_weight(10_000) > 0.99


def test_staleness_factor_no_age_returns_one() -> None:
    assert staleness_factor(None) == 1.0
    assert staleness_factor(0.0) == 1.0


def test_staleness_factor_half_life_halves() -> None:
    assert pytest.approx(staleness_factor(30.0, half_life_days=30.0), rel=1e-6) == 0.5


def test_staleness_factor_quartiles() -> None:
    # Two half-lives => quarter
    assert pytest.approx(staleness_factor(60.0, half_life_days=30.0), rel=1e-6) == 0.25
