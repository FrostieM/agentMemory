"""Phase 1 of the memory-as-organ evolution: outcome_score recomputation.

Every knowledge row (decision / theory / behavior / skill / insight / chunk)
carries a denormalized ``outcome_score`` in ``[-1.0, 1.0]``. Brief and lint
sort and filter on it; the cron sweep refreshes it on a schedule. This
module contains the *pure* math + the per-row UPDATE helpers.

Inputs that compose the score:

* ``feedback_ewma`` (existing column on most kinds) -- aggregated explicit /
  implicit usage feedback, already in ``[-1, 1]``.
* ``last_retrieved_at`` or ``last_applied_at`` / ``last_invoked_at`` --
  decays the feedback so an old "thumbs up" cannot outshine current evidence.
* ``status`` -- archived / rejected / superseded yields a hard negative.
* ``usage_count`` / ``application_count`` -- evidence weight, Wilson smoother.

The math reuses ``capability/maturity.py`` primitives (``evidence_weight``,
``staleness_factor``) so capability confidence and outcome_score age along
the same curve -- this keeps the brief's "Active decisions" and the
``hygiene_report``'s "stale capabilities" view in agreement.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.capability.maturity import evidence_weight, staleness_factor

# Hard penalties for terminal statuses. Archive is the strongest signal
# the operator can give without writing a behavior rule, so it pulls a
# row out of "Active" forever (subject to bi-temporal valid_to in Phase 6).
ARCHIVED_PENALTY = -0.8
SUPERSEDED_PENALTY = -0.5
REJECTED_PENALTY = -0.6


@dataclass(frozen=True, slots=True)
class OutcomeInputs:
    """Pure inputs to ``compute_outcome``."""

    feedback_ewma: float
    age_days: float | None
    archived: bool
    superseded: bool
    rejected: bool
    usage_count: int


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def compute_outcome(inputs: OutcomeInputs) -> float:
    """Combine EWMA + evidence weight + staleness + status into [-1, 1].

    Single-trial items are capped because ``evidence_weight`` is sub-linear
    for low ``usage_count`` -- guards against confabulation (single
    positive call should not promote a row to high confidence).
    """
    if inputs.archived:
        return -1.0
    weight = evidence_weight(inputs.usage_count)
    stale = staleness_factor(inputs.age_days)
    adjustment = inputs.feedback_ewma * weight * stale
    if inputs.superseded:
        adjustment += SUPERSEDED_PENALTY
    if inputs.rejected:
        adjustment += REJECTED_PENALTY
    return _clamp(adjustment)


# ============================================================
# Per-kind row → OutcomeInputs adapters
# ============================================================


def _age_days(now_iso: str, last_iso: str | None) -> float | None:
    if not last_iso:
        return None
    from agent_memory_lite.utils.time import parse_iso  # noqa: PLC0415

    try:
        now = parse_iso(now_iso)
        last = parse_iso(last_iso)
    except (TypeError, ValueError):
        return None
    delta = (now - last).total_seconds()
    return delta / 86400.0 if delta > 0 else 0.0


def _decision_inputs(row: sqlite3.Row, now_iso: str) -> OutcomeInputs:
    status = (row["status"] or "").lower()
    return OutcomeInputs(
        feedback_ewma=float(row["feedback_ewma"] or 0.0),
        age_days=_age_days(now_iso, row["last_retrieved_at"]),
        archived=status == "archived",
        # ``supersedes_decision_id`` on this row means THIS row replaces
        # an older one (it is the NEW decision, not the superseded one).
        # The OLD decision is the one whose status the writer pipeline
        # flips to ``superseded`` via close_decision(). So the only
        # correct signal is ``status == 'superseded'`` -- using
        # supersedes_decision_id here would invert the semantic and drop
        # every winner's outcome by ~0.5.
        superseded=status == "superseded",
        rejected=False,
        usage_count=0,  # decisions don't expose a direct usage counter
    )


def _theory_inputs(row: sqlite3.Row, now_iso: str) -> OutcomeInputs:
    status = (row["status"] or "").lower()
    return OutcomeInputs(
        feedback_ewma=float(row["feedback_ewma"] or 0.0),
        age_days=_age_days(now_iso, row["last_retrieved_at"]),
        archived=status == "archived",
        superseded=False,
        rejected=status in {"rejected", "weakened"},
        usage_count=int(row["evidence_count"] or 0),
    )


def _behavior_inputs(row: sqlite3.Row, now_iso: str) -> OutcomeInputs:
    return OutcomeInputs(
        feedback_ewma=0.0,  # behaviors don't carry EWMA; use application_count
        age_days=_age_days(now_iso, row["last_applied_at"]),
        archived=not bool(row["active"]),
        superseded=False,
        rejected=False,
        usage_count=int(row["application_count"] or 0),
    )


def _skill_inputs(row: sqlite3.Row, now_iso: str) -> OutcomeInputs:
    usage = int(row["usage_count"] or 0)
    success = int(row["success_count"] or 0)
    fail = int(row["failure_count"] or 0)
    rate = (success / (success + fail)) if (success + fail) > 0 else 0.5
    return OutcomeInputs(
        feedback_ewma=2.0 * rate - 1.0,  # map [0, 1] → [-1, 1]
        age_days=_age_days(now_iso, row["last_invoked_at"]),
        archived=not bool(row["active"]),
        superseded=False,
        rejected=False,
        usage_count=usage,
    )


def _insight_inputs(row: sqlite3.Row, now_iso: str) -> OutcomeInputs:
    status = (row["status"] or "").lower()
    return OutcomeInputs(
        feedback_ewma=float(row["confidence"] or 0.0) * 2.0 - 1.0,
        age_days=_age_days(now_iso, row["updated_at"]),
        archived=status in {"archived", "rejected"},
        superseded=False,
        rejected=status == "rejected",
        usage_count=1,
    )


def _chunk_inputs(row: sqlite3.Row, now_iso: str) -> OutcomeInputs:
    return OutcomeInputs(
        feedback_ewma=float(row["feedback_ewma"] or 0.0),
        age_days=_age_days(now_iso, row["last_retrieved_at"]),
        archived=bool(row["is_archived"]),
        superseded=False,
        rejected=False,
        usage_count=0,
    )


_ADAPTERS = {
    "decision": ("decisions", _decision_inputs),
    "theory": ("theories", _theory_inputs),
    "behavior": ("behaviors", _behavior_inputs),
    "skill": ("skills", _skill_inputs),
    "insight": ("insights", _insight_inputs),
    "chunk": ("chunks", _chunk_inputs),
}


# ============================================================
# Public API
# ============================================================


def refresh_one(
    conn: sqlite3.Connection, *, kind: str, object_id: str, now_iso: str
) -> float | None:
    """Recompute and persist outcome_score for one row. Returns new score."""
    if kind not in _ADAPTERS:
        return None
    table, adapter = _ADAPTERS[kind]
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (object_id,)).fetchone()
    if row is None:
        return None
    score = compute_outcome(adapter(row, now_iso))
    conn.execute(f"UPDATE {table} SET outcome_score = ? WHERE id = ?", (score, object_id))
    return score


def refresh_workspace(
    conn: sqlite3.Connection, *, workspace_id: str, now_iso: str, batch: int = 500
) -> dict[str, int]:
    """Recompute outcome_score for every row in this workspace.

    Returns ``{kind: updated_count}``. Idempotent -- second pass writes
    only when the computed score actually differs.
    """
    updated: dict[str, int] = {}
    for kind, (table, adapter) in _ADAPTERS.items():
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE workspace_id = ? LIMIT ?", (workspace_id, batch)
        ).fetchall()
        n = 0
        for row in rows:
            new_score = compute_outcome(adapter(row, now_iso))
            existing = float(row["outcome_score"] or 0.0)
            if abs(new_score - existing) >= 1e-4:
                conn.execute(
                    f"UPDATE {table} SET outcome_score = ? WHERE id = ?", (new_score, row["id"])
                )
                n += 1
        updated[kind] = n
    return updated
