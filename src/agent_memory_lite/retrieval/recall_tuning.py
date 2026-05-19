"""v3.1 Vector 2 MVP — adaptive recall defaults.

Per ``docs/V3_1_BREAKTHROUGH_ROADMAP.md`` Vector 2, recall shifts from
"agent guesses depth/floor" to "memory suggests them from what worked
recently". Two functions:

* ``record_outcome`` — append one ``recall_history`` row with params
  + outcome stats. Pure observability; never affects recall semantics.
* ``suggest_params`` — roll up recent history and return tuned
  ``(depth, outcome_floor)`` OR ``None`` when signal is thin.

Heuristic (first principles, not RL): strong avg outcome → raise floor
to drop noise; high empty-result rate → depth+1, lower floor. Neither
fires → ``None`` (keep caller default). The recall handler applies
the hint only when the caller passed no explicit param.

Settings (all read fresh per call): ``MEMORY_RECALL_TUNING_ENABLED``
(default true), ``..._WINDOW_HOURS`` (72), ``..._MIN_SAMPLES`` (8).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from agent_memory_lite.utils.time import iso_now


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, floor: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(floor, int(raw))
    except ValueError:
        return default


def is_tuning_enabled() -> bool:
    return _bool_env("MEMORY_RECALL_TUNING_ENABLED", True)


def window_hours() -> int:
    return _int_env("MEMORY_RECALL_TUNING_WINDOW_HOURS", 72)


def min_samples() -> int:
    return _int_env("MEMORY_RECALL_TUNING_MIN_SAMPLES", 8)


def _normalize_topic(topic: str) -> str:
    return topic.strip().lower()[:80]


@dataclass(frozen=True, slots=True)
class RecallSuggestion:
    """Tuning hint returned by ``suggest_params``.

    ``reason`` is one of ``"raise_floor"`` or ``"increase_depth"``.
    When no rule fires, ``suggest_params`` returns ``None`` rather
    than a ``RecallSuggestion`` with a sentinel reason — the handler
    treats ``None`` as "keep caller default".
    """

    depth: int
    outcome_floor: float
    reason: str


def record_outcome(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    topic: str,
    depth: int,
    outcome_floor: float,
    hits_count: int,
    avg_outcome: float | None,
    avg_activation: float | None,
) -> None:
    """Append one recall_history row. No-op when tuning disabled."""
    if not is_tuning_enabled():
        return
    try:
        conn.execute(
            "INSERT INTO recall_history (workspace_id, topic_norm, depth, "
            "outcome_floor_x100, hits_count, avg_outcome_x100, "
            "avg_activation_x1000, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workspace_id,
                _normalize_topic(topic),
                int(depth),
                round(outcome_floor * 100.0),
                int(hits_count),
                None if avg_outcome is None else round(avg_outcome * 100.0),
                None if avg_activation is None else round(avg_activation * 1000.0),
                iso_now(),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        # Pre-migration DB — silent skip keeps the read-path failure-soft.
        return


def _rollup_stats(
    conn: sqlite3.Connection, *, workspace_id: str
) -> tuple[int, float, float] | None:
    """Read recent recall_history and return (n, empty_rate, mean_outcome).

    Returns ``None`` when the table is missing or empty. Splits out the
    SQL so ``suggest_params`` stays under the return-count linter cap.
    """
    cutoff_hours = window_hours()
    cutoff = f"datetime('now', '-{cutoff_hours} hours')"
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n, "
            f"AVG(CASE WHEN hits_count = 0 THEN 1.0 ELSE 0.0 END) AS empty_rate, "
            f"AVG(COALESCE(avg_outcome_x100, 0)) AS mean_outcome_x100 "
            f"FROM recall_history "
            f"WHERE workspace_id = ? AND created_at >= {cutoff}",
            (workspace_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    if isinstance(row, sqlite3.Row):
        n = int(row["n"] or 0)
        empty_rate = float(row["empty_rate"] or 0.0)
        mean_outcome_x100 = float(row["mean_outcome_x100"] or 0.0)
    else:
        n = int(row[0] or 0)
        empty_rate = float(row[1] or 0.0)
        mean_outcome_x100 = float(row[2] or 0.0)
    return n, empty_rate, mean_outcome_x100 / 100.0


def suggest_params(
    conn: sqlite3.Connection, *, workspace_id: str, base_depth: int, base_floor: float
) -> RecallSuggestion | None:
    """Roll up recent recall_history and propose tuned params.

    Returns ``None`` when (a) tuning disabled, (b) not enough samples,
    or (c) no rule fires. Caller treats ``None`` as "keep default".
    """
    if not is_tuning_enabled():
        return None
    stats = _rollup_stats(conn, workspace_id=workspace_id)
    if stats is None:
        return None
    n, empty_rate, mean_outcome = stats
    if n < min_samples():
        return None
    if empty_rate > 0.4 and base_depth < 3:
        return RecallSuggestion(
            depth=min(3, base_depth + 1),
            outcome_floor=max(-1.0, base_floor - 0.1),
            reason="increase_depth",
        )
    if mean_outcome >= 0.3:
        return RecallSuggestion(
            depth=base_depth,
            outcome_floor=min(1.0, base_floor + 0.1),
            reason="raise_floor",
        )
    return None
