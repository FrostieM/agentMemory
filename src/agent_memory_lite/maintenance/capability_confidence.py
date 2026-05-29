"""Feed capability maturity counters into stored confidence (release task #121).

Capabilities accumulate invocation + outcome counters (``usage_count``,
``success_count``, ``failure_count`` via :mod:`capability.usage_tracker`), but
until this loop nothing ever fed those counters back into the stored
``confidence`` — the pure :func:`capability.maturity.confidence_from_maturity`
curve existed with no callers. This brain-pass step closes the loop: a
capability that keeps succeeding earns confidence, one that keeps failing loses
it, and one that falls idle decays back toward its operator-supplied
``base_confidence`` anchor.

Idempotent (a second pass with the same counters + age makes no write),
bounded (``limit`` rows per pass), and failure-soft. The ``base_confidence``
anchor is set by the capability upsert path (and re-anchored when an operator
re-upserts with a new confidence); this loop keeps a defensive
capture-when-``NULL`` fallback for any legacy/backfilled row whose anchor was
never written. A capability with no recorded outcomes stays exactly at its
base — invocations alone are not evidence about success.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_memory_lite.capability.maturity import MaturityInputs, confidence_from_maturity
from agent_memory_lite.capability.usage_tracker import SUPPORTED_KINDS

# Confidence deltas below this are not worth a write — keeps ``updated_at``
# stable for capabilities whose maturity-adjusted confidence did not move.
_EPSILON = 1e-4
DEFAULT_MAX_PER_PASS = 500


@dataclass(frozen=True, slots=True)
class ConfidenceRecomputeStats:
    scanned: int = 0
    updated: int = 0


def _age_days(last_invoked_at: str | None, now: datetime) -> float | None:
    """Whole-and-fractional days since last invocation, or ``None`` if never /
    unparseable (the curve treats ``None`` as 'no decay applied')."""
    if not last_invoked_at:
        return None
    try:
        ts = datetime.fromisoformat(last_invoked_at)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (now - ts).total_seconds() / 86400.0)


def recompute_capability_confidence(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    half_life_days: float = 30.0,
    now: datetime | None = None,
    limit: int = DEFAULT_MAX_PER_PASS,
) -> ConfidenceRecomputeStats:
    """Refresh ``skills.confidence`` from the maturity curve for active
    capabilities. Returns counts of rows scanned and rows whose confidence
    actually changed. Does not commit — the caller owns the transaction."""
    moment = now or datetime.now(UTC)
    now_iso = moment.isoformat()
    placeholders = ",".join("?" for _ in SUPPORTED_KINDS)
    rows = conn.execute(
        f"""
        SELECT id, confidence, base_confidence,
               usage_count, success_count, failure_count, last_invoked_at
        FROM skills
        WHERE workspace_id = ?
          AND active = 1
          AND subtype IN ({placeholders})
        ORDER BY updated_at ASC, id ASC
        LIMIT ?
        """,
        (workspace_id, *SUPPORTED_KINDS, int(limit)),
    ).fetchall()

    scanned = 0
    updated = 0
    for row in rows:
        scanned += 1
        current = float(row["confidence"] or 0.0)
        raw_base = row["base_confidence"]
        base = float(raw_base) if raw_base is not None else current
        success = int(row["success_count"] or 0)
        failure = int(row["failure_count"] or 0)
        if success + failure == 0:
            # No recorded outcomes -> no evidence about whether this capability
            # actually works, so confidence stays at the operator base. The
            # common skill path bumps usage_count without ever recording an
            # outcome; without this guard the curve's neutral 0.5 success-rate
            # prior (carried by a usage-driven evidence weight) would drag such a
            # capability's confidence toward 0.5 -- a silent regression.
            new_conf = base
        else:
            new_conf = confidence_from_maturity(
                MaturityInputs(
                    base=base,
                    usage_count=int(row["usage_count"] or 0),
                    success_count=success,
                    failure_count=failure,
                    age_days_since_last_use=_age_days(row["last_invoked_at"], moment),
                ),
                half_life_days=half_life_days,
            )
        conf_changed = abs(new_conf - current) >= _EPSILON
        base_missing = raw_base is None
        if conf_changed:
            # Bump updated_at only when confidence actually moves; persist the
            # captured base in the same write.
            conn.execute(
                "UPDATE skills SET confidence = ?, base_confidence = ?, updated_at = ? "
                "WHERE id = ? AND workspace_id = ?",
                (new_conf, base, now_iso, row["id"], workspace_id),
            )
            updated += 1
        elif base_missing:
            # First visit, confidence unchanged (no outcomes yet) — capture the
            # anchor without touching updated_at (bookkeeping, not a content edit).
            conn.execute(
                "UPDATE skills SET base_confidence = ? WHERE id = ? AND workspace_id = ?",
                (base, row["id"], workspace_id),
            )
    return ConfidenceRecomputeStats(scanned=scanned, updated=updated)
