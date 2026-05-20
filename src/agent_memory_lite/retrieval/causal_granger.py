"""v3.4 #7 — V4 method (b): Granger-style lead-lag causality.

V4's roadmap names three approaches for learned causality:

* (a) DiD on supersede events — ``causal_did`` (shipped v3.3 #58).
* (b) **Granger causality on usage time series** — THIS module.
* (c) Counterfactual via embedding similarity — ``causal_embedding``
  (shipped v3.1 Vector 4).

With (b) shipped the V4 layer is triple-sourced: DiD answers "did the
replacement help?", embedding answers "is this descendant semantically
close to its parent?", Granger answers "does activity on X precede
activity on Y across the corpus?". When the same ordered pair shows
up under multiple relations, the recall layer reads stronger
confidence — the multi-method confirmation the roadmap called for.

Method, in plain terms:
~~~~~~~~~~~~~~~~~~~~~~

For every (decision/theory) row touched in the last
``MEMORY_CAUSAL_GRANGER_WINDOW_DAYS`` (default 30) we build a per-day
activity vector from ``memory_usage_feedback``: a 1 on days the object
was retrieved or linked, 0 otherwise. For each ordered pair (X, Y)
that has at least ``MEMORY_CAUSAL_GRANGER_MIN_ACTIVITY`` (default 3)
non-zero days each, we compute two Pearson correlations:

* ``r_xy_lag`` = corr(X_{t-1}, Y_t) — does past X predict current Y?
* ``r_yy_lag`` = corr(Y_{t-1}, Y_t) — does Y predict itself?

When ``|r_xy_lag| - |r_yy_lag| >= MEMORY_CAUSAL_GRANGER_THRESHOLD``
(default 0.25) AND ``r_xy_lag > 0`` (positive lead-lag — X precedes Y,
not just anti-correlated), we emit a
``causal_link(relation='granger_caused', weight=|r_xy_lag|)``.

This is NOT the textbook Granger F-test (which would compare residual
variances of an AR(k) vs ARX(k) model). It IS the same intuition —
"does X's history reduce surprise about Y's future?" — at O(n) per
pair using pure ``statistics.correlation``, no NumPy / no model-fitting
machinery. The simplification is documented so the next agent doesn't
mistake the strength weight for a p-value.

Safety:

* Failure-soft: missing ``memory_usage_feedback`` / ``causal_links``
  columns return an empty report so ``brain_pass`` keeps moving.
* Pair count capped at
  ``MEMORY_CAUSAL_GRANGER_MAX_PAIRS`` (default 200) so a workspace
  with 50 active decisions doesn't pay 2500 correlations per tick.
* Idempotent via the existing UNIQUE constraint on causal_links.
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import StatisticsError, correlation

from agent_memory_lite.retrieval.causal_extractor import _upsert_causal_link


@dataclass(frozen=True, slots=True)
class GrangerReport:
    """Per-workspace Granger summary surfaced via brain_pass."""

    pairs_scanned: int
    links_emitted: int


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


def is_enabled() -> bool:
    """v3.4: default ON. Operator sets
    ``MEMORY_CAUSAL_GRANGER_ENABLED=false`` to keep V4 single-sourced
    on DiD + embeddings."""
    return _bool_env("MEMORY_CAUSAL_GRANGER_ENABLED", True)


def window_days() -> int:
    return _int_env("MEMORY_CAUSAL_GRANGER_WINDOW_DAYS", 30, floor=4)


def threshold() -> float:
    """Min ``|r_xy_lag| - |r_yy_lag|`` to emit a link. 0.25 keeps the
    signal-to-noise ratio reasonable on small workspaces (a 30-bucket
    series of mostly-zero days can hit ±0.15 from coincidence)."""
    return _float_env("MEMORY_CAUSAL_GRANGER_THRESHOLD", 0.25)


def min_activity() -> int:
    """Minimum non-zero buckets per series. Below this a correlation
    is dominated by the zero-zero pairs and is meaningless."""
    return _int_env("MEMORY_CAUSAL_GRANGER_MIN_ACTIVITY", 3)


def max_pairs() -> int:
    """Cap on (X, Y) pairs examined per pass. Keeps brain_pass cost
    O(window_days * max_pairs)."""
    return _int_env("MEMORY_CAUSAL_GRANGER_MAX_PAIRS", 200, floor=4)


def _load_activity_series(
    conn: sqlite3.Connection, *, workspace_id: str, days: int
) -> dict[tuple[str, str], list[int]]:
    """Return ``(source_type, source_id) -> [activity per day]`` for
    the last ``days`` days. activity[0] is the oldest bucket; activity[-1]
    is today. Each entry counts ``memory_usage_feedback`` rows for that
    object on that day — proxy for "how often was it touched"."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    try:
        rows = conn.execute(
            """SELECT source_type, source_id, substr(created_at, 1, 10) AS day
                 FROM memory_usage_feedback
                WHERE workspace_id = ?
                  AND source_type IN ('decision', 'theory')
                  AND substr(created_at, 1, 10) >= ?""",
            (workspace_id, cutoff),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    today = datetime.now(UTC).date()
    day_index: dict[str, int] = {
        (today - timedelta(days=days - 1 - i)).isoformat(): i for i in range(days)
    }
    buckets: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * days)
    for row in rows:
        if isinstance(row, sqlite3.Row):
            st, sid, day = row["source_type"], row["source_id"], row["day"]
        else:
            st, sid, day = row[0], row[1], row[2]
        idx = day_index.get(str(day))
        if idx is None:
            continue
        buckets[(str(st), str(sid))][idx] += 1
    return dict(buckets)


def _lead_lag_strength(x: list[int], y: list[int]) -> float:
    """Return ``|corr(X_{t-1}, Y_t)| - |corr(Y_{t-1}, Y_t)|`` clamped at 0.

    Negative result = X tells us less about Y's future than Y itself
    already does (no Granger signal). Positive = X's history adds
    predictive power above Y's autocorrelation. We return the gap
    rather than the raw r_xy_lag so the threshold has a stable
    interpretation across series of different self-correlation."""
    if len(x) != len(y) or len(x) < 3:
        return 0.0
    x_lag = [float(v) for v in x[:-1]]
    y_lag = [float(v) for v in y[:-1]]
    y_t = [float(v) for v in y[1:]]
    try:
        r_xy = correlation(x_lag, y_t)
        r_yy = correlation(y_lag, y_t)
    except StatisticsError:
        # statistics.correlation raises when a series is constant.
        return 0.0
    # Positive directional signal only: anti-correlation isn't Granger.
    if r_xy <= 0:
        return 0.0
    return max(0.0, abs(r_xy) - abs(r_yy))


def extract_granger_links(conn: sqlite3.Connection, *, workspace_id: str) -> GrangerReport:
    """Scan recent usage activity, emit ``granger_caused`` links where
    the lead-lag gap crosses the threshold. Idempotent."""
    if not is_enabled():
        return GrangerReport(pairs_scanned=0, links_emitted=0)
    series = _load_activity_series(conn, workspace_id=workspace_id, days=window_days())
    if len(series) < 2:
        return GrangerReport(pairs_scanned=0, links_emitted=0)
    min_act = min_activity()
    # Filter to series with enough non-zero buckets — keeps noise out.
    active = [(key, vec) for key, vec in series.items() if sum(1 for v in vec if v) >= min_act]
    if len(active) < 2:
        return GrangerReport(pairs_scanned=0, links_emitted=0)
    cap = max_pairs()
    delta_threshold = threshold()
    pairs = 0
    emitted = 0
    # Order pairs deterministically so re-runs touch the same set.
    active.sort(key=lambda kv: kv[0])
    for i, (key_x, vec_x) in enumerate(active):
        for key_y, vec_y in active[i + 1 :]:
            if pairs >= cap:
                break
            pairs += 1
            # Check both directions independently — Granger asymmetry
            # is the whole point: X may lead Y without Y leading X.
            for src, dst, vec_a, vec_b in (
                (key_x, key_y, vec_x, vec_y),
                (key_y, key_x, vec_y, vec_x),
            ):
                gap = _lead_lag_strength(vec_a, vec_b)
                if gap < delta_threshold:
                    continue
                if _upsert_causal_link(
                    conn,
                    workspace_id=workspace_id,
                    src_kind=src[0],
                    src_id=src[1],
                    dst_kind=dst[0],
                    dst_id=dst[1],
                    relation="granger_caused",
                    weight=float(gap),
                ):
                    emitted += 1
        if pairs >= cap:
            break
    if emitted:
        conn.commit()
    return GrangerReport(pairs_scanned=pairs, links_emitted=emitted)
