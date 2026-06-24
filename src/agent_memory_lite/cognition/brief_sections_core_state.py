"""Brief task-state builder — the active-task "State" section plus its
task-row query helpers.

Extracted from cognition/brief_sections_core.py during a SLOC
decomposition. Behavior is identical; brief_sections_core re-exports
``_build_state`` (and ``_count_open_tasks``, used by the identity
builder) so the original module path keeps its public surface.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_tokens import fit_to_budget

_OPEN_TASK_STATUSES = ("active", "in_progress")


def _blockers_count_from_json(value: object) -> int:
    if not isinstance(value, str) or not value.strip():
        return 0
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def _count_open_tasks(conn: sqlite3.Connection, workspace_id: str) -> int:
    try:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE workspace_id = ? AND status IN ('active', 'in_progress')
                """,
                (workspace_id,),
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        return 0


def _open_task_rows(
    conn: sqlite3.Connection, workspace_id: str, *, limit: int
) -> list[dict[str, object]]:
    try:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE workspace_id = ? AND status IN ('active', 'in_progress')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["blockers_count"] = _blockers_count_from_json(item.get("blockers_json"))
        out.append(item)
    return out


def _build_state(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Workspace-aware (P2): emit nothing on a workspace with no active
    tasks. The freed budget is reallocated by ``_redistribute_and_rebuild``
    in ``compose_brief``: when this section returns empty, denser
    sections (identity / behaviors / decisions / aging_decisions) get a
    proportional bonus + re-render with bigger caps.
    """
    rows = _open_task_rows(conn, workspace_id, limit=3)
    if not rows:
        return BriefSection(name="state", budget=budget, lines=[])
    lines = ["## State"]
    for t in rows:
        goal = t.get("goal_one_line") or "?"
        status = t.get("status", "?")
        next_action = t.get("next_action") or "(none)"
        blockers = t.get("blockers_count", 0)
        lines.append(
            f"- task {t.get('task_id', '?')} [{status}]: {goal} "
            f"→ next: {next_action} (blockers: {blockers})"
        )
    fitted = fit_to_budget(lines, budget)
    if not any(line.startswith("- task ") for line in fitted):
        return BriefSection(name="state", budget=budget, lines=[])
    return BriefSection(name="state", budget=budget, lines=fitted)
