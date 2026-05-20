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

Three-tier transfer-learning bootstrap:

1. **Own workspace** — same SQLite, same workspace_id, last
   ``window_hours`` rows. Lowest noise, lowest bar (``min_samples=8``).
2. **Same-DB transfer** (v3.3, ``MEMORY_RECALL_TRANSFER_ENABLED=true``,
   default). When tier 1 is thin, aggregate ALL workspaces in the
   same SQLite. Higher bar (``transfer_min_samples=16``) because
   sibling workspaces may be mixed-domain.
3. **Cross-DB transfer** (v3.4 #9,
   ``MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED=true``, default OFF).
   When tier 2 is also thin, walk ``~/.agent_memory/workspaces.json``
   and aggregate ``recall_history`` from every OTHER registered DB.
   Highest bar (``..._MIN_SAMPLES=32``). The actual cross-DB scan
   lives in ``recall_tuning_cross_db`` so that module owns the
   registry iteration + per-peer connection lifecycle.

Settings (all read fresh per call): ``MEMORY_RECALL_TUNING_ENABLED``
(default true), ``..._WINDOW_HOURS`` (72), ``..._MIN_SAMPLES`` (8),
``MEMORY_RECALL_TRANSFER_ENABLED`` (default true),
``MEMORY_RECALL_TRANSFER_MIN_SAMPLES`` (default 16 — higher bar than
own-workspace samples because cross-workspace signal is noisier),
``MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED`` (default false),
``MEMORY_RECALL_TRANSFER_CROSS_DB_MIN_SAMPLES`` (default 32).
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


def is_transfer_enabled() -> bool:
    """v3.3: default ON. Set ``MEMORY_RECALL_TRANSFER_ENABLED=false``
    to restore strict per-workspace tuning (no cross-pollination)."""
    return _bool_env("MEMORY_RECALL_TRANSFER_ENABLED", True)


def transfer_min_samples() -> int:
    """Higher bar than own-workspace samples — cross-workspace signal
    is mixed-domain so we need more rows to trust the rollup."""
    return _int_env("MEMORY_RECALL_TRANSFER_MIN_SAMPLES", 16)


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
    conn: sqlite3.Connection, *, workspace_id: str | None
) -> tuple[int, float, float] | None:
    """Read recent recall_history and return (n, empty_rate, mean_outcome).

    Pass ``workspace_id=None`` to aggregate across ALL workspaces in
    the same DB — the v3.3 transfer-learning path. Returns ``None`` when
    the table is missing or empty.
    """
    cutoff_hours = window_hours()
    cutoff = f"datetime('now', '-{cutoff_hours} hours')"
    where = "created_at >= " + cutoff
    params: tuple[str, ...] = ()
    if workspace_id is not None:
        where = "workspace_id = ? AND " + where
        params = (workspace_id,)
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n, "
            f"AVG(CASE WHEN hits_count = 0 THEN 1.0 ELSE 0.0 END) AS empty_rate, "
            f"AVG(COALESCE(avg_outcome_x100, 0)) AS mean_outcome_x100 "
            f"FROM recall_history WHERE {where}",
            params,
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


def _apply_rules(
    *, stats: tuple[int, float, float], base_depth: int, base_floor: float, reason_prefix: str
) -> RecallSuggestion | None:
    """Apply the two heuristic rules to a rollup snapshot.

    Returns ``None`` when no rule fires. ``reason_prefix`` is "" for
    own-workspace and "transfer:" for the v3.3 fallback path so the
    operator can grep audit rows by source.
    """
    _n, empty_rate, mean_outcome = stats
    if empty_rate > 0.4 and base_depth < 3:
        return RecallSuggestion(
            depth=min(3, base_depth + 1),
            outcome_floor=max(-1.0, base_floor - 0.1),
            reason=f"{reason_prefix}increase_depth",
        )
    if mean_outcome >= 0.3:
        return RecallSuggestion(
            depth=base_depth,
            outcome_floor=min(1.0, base_floor + 0.1),
            reason=f"{reason_prefix}raise_floor",
        )
    return None


def suggest_params(  # noqa: PLR0911 — linear three-tier fallback; splitting hurts readability
    conn: sqlite3.Connection, *, workspace_id: str, base_depth: int, base_floor: float
) -> RecallSuggestion | None:
    """Roll up recent recall_history and propose tuned params.

    Three-tier strategy:

    1. Own-workspace rollup over the last ``window_hours``. If
       ``min_samples`` reached, apply the heuristic rules and return
       with reason prefix ``""``.
    2. v3.3 same-DB transfer fallback: aggregate ALL workspaces in
       the same DB. If ``transfer_min_samples`` reached AND
       ``MEMORY_RECALL_TRANSFER_ENABLED=true``, apply with prefix
       ``"transfer:"``.
    3. v3.4 #9 cross-DB transfer fallback: walk the workspaces
       registry, aggregate ``recall_history`` from every other
       registered DB. If ``cross_db_min_samples`` reached AND
       ``MEMORY_RECALL_TRANSFER_CROSS_DB_ENABLED=true``, apply with
       prefix ``"transfer_cross_db:"`` so audits can grep by source.
    4. Nothing fires → return ``None`` (caller keeps default).
    """
    if not is_tuning_enabled():
        return None
    stats = _rollup_stats(conn, workspace_id=workspace_id)
    if stats is not None and stats[0] >= min_samples():
        return _apply_rules(
            stats=stats, base_depth=base_depth, base_floor=base_floor, reason_prefix=""
        )
    if is_transfer_enabled():
        transfer_stats = _rollup_stats(conn, workspace_id=None)
        if transfer_stats is not None and transfer_stats[0] >= transfer_min_samples():
            return _apply_rules(
                stats=transfer_stats,
                base_depth=base_depth,
                base_floor=base_floor,
                reason_prefix="transfer:",
            )
    # v3.4 #9 tier 3: cross-DB fallback. Late import keeps this module
    # cheap when cross-DB transfer is disabled (the default).
    from agent_memory_lite.retrieval.recall_tuning_cross_db import (  # noqa: PLC0415
        cross_db_min_samples,
        is_cross_db_enabled,
        rollup_cross_db_stats,
    )

    if not is_cross_db_enabled():
        return None
    current_db = _current_db_path(conn)
    if current_db is None:
        return None
    cross_stats = rollup_cross_db_stats(current_db_path=current_db, window_hours=window_hours())
    if cross_stats is None or cross_stats[0] < cross_db_min_samples():
        return None
    return _apply_rules(
        stats=cross_stats,
        base_depth=base_depth,
        base_floor=base_floor,
        reason_prefix="transfer_cross_db:",
    )


def _current_db_path(conn: sqlite3.Connection) -> str | None:
    """Return the file path of the ``main`` schema on this connection,
    or ``None`` for in-memory / unrecognized connections. Used by tier
    3 so the cross-DB scanner can skip the current DB and avoid
    double-counting rows the same-DB tier already saw."""
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            # row shape: (seq, name, file). 'main' is the user DB.
            name = row[1] if not isinstance(row, sqlite3.Row) else row["name"]
            file_ = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
            if name == "main" and file_:
                return str(file_)
    except sqlite3.Error:
        return None
    return None
