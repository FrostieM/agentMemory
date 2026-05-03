"""Pure maturity-curve helpers.

Capability confidence is no longer a static field set at upsert time. It is
adjusted by observed outcomes through ``confidence_from_maturity``:

* base — the operator-supplied confidence at upsert time, anchors the curve.
* usage_count — Wilson-style "uses earned trust" component, asymptotes to
  the success rate as evidence accumulates.
* success_rate — successful invocations / (successful + failed).
* age_days_since_last_use — exponential decay so unused capabilities drift
  back toward the base, not toward 1.0.

The function is pure so it can be unit-tested without a DB and reused by
both the runtime tracker and the hygiene report. Bounded in [0, 1].
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# How fast the confidence drifts back toward `base` when a capability isn't
# used. 30 days = half-life. After 60 idle days, an inflated confidence is
# halved twice, surfacing the staleness without erasing all evidence.
DEFAULT_DECAY_HALF_LIFE_DAYS = 30.0

# Smoothing constant — at usage_count == 0 the curve sits at `base`; at
# very large usage_count the curve approaches the observed success rate.
EVIDENCE_HALF_POINT = 5.0


@dataclass(frozen=True, slots=True)
class MaturityInputs:
    base: float
    usage_count: int
    success_count: int
    failure_count: int
    age_days_since_last_use: float | None


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def success_rate(success_count: int, failure_count: int) -> float:
    """Observed success rate, neutral 0.5 when no evidence is available."""
    total = success_count + failure_count
    if total <= 0:
        return 0.5
    return success_count / total


def evidence_weight(usage_count: int) -> float:
    """How much to trust the observed success rate vs the operator base.

    At usage_count = 0 the weight is 0 (full trust in operator base).
    At usage_count = EVIDENCE_HALF_POINT the weight is 0.5.
    Approaches 1.0 asymptotically as usage grows.
    """
    if usage_count <= 0:
        return 0.0
    return usage_count / (usage_count + EVIDENCE_HALF_POINT)


def staleness_factor(
    age_days_since_last_use: float | None, *, half_life_days: float = DEFAULT_DECAY_HALF_LIFE_DAYS
) -> float:
    """Multiplier in (0, 1] applied to the evidence-driven adjustment.

    Newly used capabilities get factor 1.0; idle ones decay so confidence
    drifts back to the operator base instead of staying inflated forever.
    """
    if age_days_since_last_use is None or age_days_since_last_use <= 0.0:
        return 1.0
    return math.pow(0.5, age_days_since_last_use / half_life_days)


def confidence_from_maturity(
    inputs: MaturityInputs, *, half_life_days: float = DEFAULT_DECAY_HALF_LIFE_DAYS
) -> float:
    """Combine base, usage evidence, and staleness into a bounded confidence."""
    base = _clamp(inputs.base)
    rate = success_rate(inputs.success_count, inputs.failure_count)
    weight = evidence_weight(inputs.usage_count)
    stale = staleness_factor(inputs.age_days_since_last_use, half_life_days=half_life_days)
    # Adjustment toward observed rate, scaled by both the evidence weight
    # (do we trust the observation?) and the staleness factor (is the
    # observation still fresh enough to trust?).
    adjustment = (rate - base) * weight * stale
    return _clamp(base + adjustment)
