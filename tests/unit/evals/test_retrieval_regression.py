"""Shared retrieval-metric regression gate (release plan step 10.6)."""

from __future__ import annotations

import pytest

from agent_memory_lite.evals.retrieval_regression import (
    DEFAULT_REGRESSION_THRESHOLD,
    compare_metrics,
)

_METRICS = ["ndcg_at_10", "recall_at_10", "map_at_10"]


def test_no_regression_when_primary_flat_or_up() -> None:
    report = {"ndcg_at_10": 0.70, "recall_at_10": 0.60, "map_at_10": 0.55}
    baseline = {"ndcg_at_10": 0.68, "recall_at_10": 0.62, "map_at_10": 0.55}
    result = compare_metrics(report, baseline, metrics=_METRICS, primary="ndcg_at_10")
    assert result.regressed is False
    assert result.exit_code == 0
    assert result.primary_delta == pytest.approx(0.02)
    assert result.deltas["recall_at_10"] == pytest.approx(-0.02)  # a non-primary drop is ignored


def test_regression_when_primary_drops_past_threshold() -> None:
    report = {"ndcg_at_10": 0.60}
    baseline = {"ndcg_at_10": 0.70}  # -0.10 < -0.05
    result = compare_metrics(report, baseline, metrics=_METRICS, primary="ndcg_at_10")
    assert result.regressed is True
    assert result.exit_code == 1
    assert result.primary_delta == pytest.approx(-0.10)


def test_drop_exactly_at_threshold_is_allowed() -> None:
    # Only a strictly larger drop fails, matching the original membench gate.
    report = {"ndcg_at_10": 0.65}
    baseline = {"ndcg_at_10": 0.70}  # -0.05, not < -0.05
    result = compare_metrics(
        report, baseline, metrics=_METRICS, primary="ndcg_at_10", threshold=0.05
    )
    assert result.regressed is False
    assert result.exit_code == 0


def test_missing_and_nonnumeric_values_read_as_zero() -> None:
    report = {"ndcg_at_10": 0.40}  # recall/map absent
    baseline = {"ndcg_at_10": 0.40, "recall_at_10": "oops", "map_at_10": True}
    result = compare_metrics(report, baseline, metrics=_METRICS, primary="ndcg_at_10")
    assert result.deltas["recall_at_10"] == pytest.approx(0.0)  # absent report - 0 baseline
    assert result.deltas["map_at_10"] == pytest.approx(0.0)  # bool baseline coerced to 0.0
    assert result.regressed is False


def test_empty_baseline_never_regresses_on_zero_report() -> None:
    result = compare_metrics({}, {}, metrics=_METRICS, primary="ndcg_at_10")
    assert result.deltas == {"ndcg_at_10": 0.0, "recall_at_10": 0.0, "map_at_10": 0.0}
    assert result.regressed is False


def test_default_threshold_is_five_percent() -> None:
    assert DEFAULT_REGRESSION_THRESHOLD == pytest.approx(0.05)


def test_invalid_threshold_and_primary_raise() -> None:
    with pytest.raises(ValueError, match="threshold"):
        compare_metrics({}, {}, metrics=_METRICS, primary="ndcg_at_10", threshold=-0.01)
    with pytest.raises(ValueError, match="primary"):
        compare_metrics({}, {}, metrics=_METRICS, primary="not_a_metric")
