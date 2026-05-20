"""v3.4 #8 — workspace-level audit_log signals for V5 LR.

The base feature vector (predictive_lr_features) gives V5 a text BOW
plus two trend slots. Today's audit on agent-memory-lite found that
the three watch-out decisions (outcome=-1.0) ALL had the highest
audit_log row counts in the workspace — 5 each — while quiet
healthy decisions had 1 or 0. That's a near-perfect "churn predicts
trouble" signal V5 was throwing away.

This module mines audit_log into three workspace-level scalars,
normalized to [0, 1] so they slot into the LR feature vector without
re-scaling weights:

* ``velocity``        — audit_log rows per day over the last
  ``MEMORY_PREDICTIVE_LR_AUDIT_WINDOW_DAYS`` (default 7),
  divided by the velocity ceiling (default 50) and clipped to 1.
* ``edit_share``      — fraction of those rows whose ``action`` falls
  into the "mutation" set (edit / archive / reject / supersede /
  update / rollback). High edit_share = lots of churn relative to
  fresh writes.
* ``agent_diversity`` — distinct ``agent_id`` count over the same
  window, divided by the diversity ceiling (default 5). Many agents
  touching the same workspace correlates with merge-conflict-style
  trouble in field-collected data.

The signals are computed AS OF a caller-supplied timestamp so the
training pass can reconstruct historical state without leakage —
each historical sample sees only audit rows that existed before its
own created_at.

Failure-soft: missing ``audit_log`` table → zeros. Pure-stdlib so
the local-only stack stays unchanged.
"""

from __future__ import annotations

import os
import sqlite3

_MUTATING_ACTIONS = frozenset(
    {
        "edit",
        "write",
        "archive",
        "supersede",
        "reject_memory_candidate",
        "promote_candidate",
        "update_insight",
        "rollback",
        "resolve_maintenance_event",
        "dismiss_maintenance_event",
    }
)


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


def window_days() -> int:
    return _int_env("MEMORY_PREDICTIVE_LR_AUDIT_WINDOW_DAYS", 7, floor=1)


def velocity_ceiling() -> int:
    """Audit rows per day above which ``velocity`` saturates to 1."""
    return _int_env("MEMORY_PREDICTIVE_LR_AUDIT_VELOCITY_CEILING", 50, floor=1)


def diversity_ceiling() -> int:
    """Distinct agent count above which ``agent_diversity`` saturates."""
    return _int_env("MEMORY_PREDICTIVE_LR_AUDIT_DIVERSITY_CEILING", 5, floor=1)


def workspace_audit_signals(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    as_of_ts: str | None = None,
) -> tuple[float, float, float]:
    """Return ``(velocity, edit_share, agent_diversity)`` in [0, 1].

    ``as_of_ts`` clips the audit window to rows strictly before that
    ISO timestamp — used by the training pass so historical samples
    do not peek at the future. Defaults to "now" (all rows in window
    visible) for the inference path."""
    days = window_days()
    try:
        if as_of_ts is None:
            rows = conn.execute(
                "SELECT action, agent_id FROM audit_log "
                "WHERE workspace_id = ? "
                "AND created_at >= datetime('now', ?)",
                (workspace_id, f"-{days} days"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT action, agent_id FROM audit_log "
                "WHERE workspace_id = ? "
                "AND created_at < ? "
                "AND created_at >= datetime(?, ?)",
                (workspace_id, as_of_ts, as_of_ts, f"-{days} days"),
            ).fetchall()
    except sqlite3.OperationalError:
        return (0.0, 0.0, 0.0)
    total = len(rows)
    if total == 0:
        return (0.0, 0.0, 0.0)
    velocity_raw = total / float(days)
    velocity = min(1.0, velocity_raw / float(velocity_ceiling()))
    mutating = sum(
        1
        for r in rows
        if (r[0] if not isinstance(r, sqlite3.Row) else r["action"]) in _MUTATING_ACTIONS
    )
    edit_share = mutating / float(total)
    distinct = {(r[1] if not isinstance(r, sqlite3.Row) else r["agent_id"]) or "" for r in rows}
    diversity = min(1.0, len(distinct) / float(diversity_ceiling()))
    return (velocity, edit_share, diversity)


# Number of feature slots this module contributes to the LR vector.
AUDIT_FEATURE_COUNT = 3
