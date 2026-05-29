"""Shared retrieval-metric regression comparison (release plan step 10.6).

Both the internal sanity bench (``scripts/membench.py``, MRR over a workspace's
own decisions) and the stable full-stack BEIR bench
(``scripts/membench_pipeline.py``, NDCG@10 over a fixed corpus) need the same
thing: compare this run's metrics against a saved baseline and fail when the
primary metric drops by more than a threshold. This is that one pure, testable
comparison so the two benches cannot drift apart and so the gate logic can be
unit-tested without a live service, ``mteb``, or a real corpus.

The BEIR pipeline bench runs over a FIXED corpus, so its baseline is stable and
a committed baseline is meaningful. The internal decision bench runs over a
MUTABLE corpus, so its baseline is only comparable within a stable decision set
-- the caller owns that caveat; this module just does the arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_REGRESSION_THRESHOLD = 0.05


@dataclass(frozen=True, slots=True)
class RegressionResult:
    deltas: dict[str, float]
    regressed: bool
    primary_metric: str
    primary_delta: float

    @property
    def exit_code(self) -> int:
        """1 when the primary metric regressed past the threshold, else 0 --
        so a CLI can ``return result.exit_code`` directly as a gate."""
        return 1 if self.regressed else 0


def compare_metrics(
    report: dict[str, object],
    baseline: dict[str, object],
    *,
    metrics: list[str],
    primary: str,
    threshold: float = DEFAULT_REGRESSION_THRESHOLD,
) -> RegressionResult:
    """Compare ``report`` against ``baseline`` for the named ``metrics``.

    ``deltas[m] = report[m] - baseline[m]`` (missing values read as 0.0). The
    run has *regressed* when the ``primary`` metric fell by more than
    ``threshold`` (a drop of exactly ``threshold`` is allowed -- only a strictly
    larger drop fails, matching the original membench gate). ``threshold`` must
    be non-negative.
    """
    if threshold < 0:
        raise ValueError(f"threshold must be >= 0, got {threshold}")
    if primary not in metrics:
        raise ValueError(f"primary metric {primary!r} must be one of {metrics}")
    deltas = {
        metric: _as_float(report.get(metric)) - _as_float(baseline.get(metric))
        for metric in metrics
    }
    primary_delta = deltas[primary]
    return RegressionResult(
        deltas=deltas,
        regressed=primary_delta < -threshold,
        primary_metric=primary,
        primary_delta=primary_delta,
    )


def _as_float(value: object) -> float:
    if isinstance(value, bool):  # bool is an int subclass; never a metric value
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0
