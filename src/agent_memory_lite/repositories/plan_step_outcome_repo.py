"""SQL for the Phase 5c plan-step -> capability-maturity feed.

Split out of ``plan_step_repo.py`` so that file stays under the SLOC
ceiling. Two queries: select the terminal steps whose outcome has not
been fed into capability maturity yet, and stamp a step once its
outcome has been fed.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.plan_step import PlanStep
from agent_memory_lite.repositories.plan_step_repo import row_to_plan_step


def terminal_steps_pending_outcome(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    limit: int,
) -> list[PlanStep]:
    """Live steps in a terminal status whose outcome has not yet been fed
    into capability maturity (``outcome_fed_at IS NULL``).

    Terminal = ``done`` / ``skipped`` only. ``blocked`` is excluded: it
    is a transient state a step recovers from, so feeding it would stamp
    a permanent outcome before the step truly settled. ``valid_to IS
    NULL`` keeps re-planned-out steps out. Ordered by ``updated_at`` so a
    capped pass takes the longest-settled steps first.
    """
    rows = conn.execute(
        """
        SELECT * FROM plan_steps
        WHERE workspace_id = ?
          AND valid_to IS NULL
          AND status IN ('done', 'skipped')
          AND outcome_fed_at IS NULL
        ORDER BY updated_at
        LIMIT ?
        """,
        (workspace_id, limit),
    ).fetchall()
    return [row_to_plan_step(r) for r in rows]


def mark_outcome_fed(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    step_id: str,
    fed_at: str,
) -> None:
    """Stamp ``outcome_fed_at`` so the brain pass never re-feeds this step.

    Sets only ``outcome_fed_at`` -- deliberately NOT ``updated_at`` -- so
    this maintenance write neither churns the brief cache nor disturbs
    the ``terminal_steps_pending_outcome`` ordering.
    """
    conn.execute(
        "UPDATE plan_steps SET outcome_fed_at = ? WHERE workspace_id = ? AND id = ?",
        (fed_at, workspace_id, step_id),
    )
