"""Brief section builder — the workspace's active plan.

Phase 3 of the plan-storage redesign. The plan is pinned into the brief
but rendered compact: goal + done/total count, the step in progress, any
blocked steps, the next pending step titles. The full plan and per-step
detail stay fetch-on-demand via memory_get / memory_search.

Free text is word-capped so a verbose title or body cannot crowd out the
essential in-progress line; when even the capped in-progress line will
not fit the budget, the whole section is dropped (its budget is
redistributed) rather than left as a misleading contentless header.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_tokens import fit_to_budget
from agent_memory_lite.models.plan_step import PlanStep
from agent_memory_lite.repositories.plan_step_repo import list_plan_steps

_NEXT_STEPS_SHOWN = 2

# Word caps for free text. The active-plan budget is tight (~35 tokens at
# the default 500-token brief), so an uncapped title / body / goal would
# let one verbose step push the in-progress line out of the section.
_GOAL_WORDS = 10
_TITLE_WORDS = 10
_BODY_WORDS = 16

# The most-recently-updated in-progress task that owns at least one live
# plan step. The EXISTS sub-query filters BEFORE the LIMIT, so the
# plan-bearing task is found no matter how many plan-less in-progress
# tasks happen to have been touched more recently.
_PLAN_TASK_SQL = (
    "SELECT t.task_id AS task_id, t.goal_one_line AS goal_one_line FROM tasks t "
    "WHERE t.workspace_id = ? AND t.status = 'in_progress' "
    "AND EXISTS (SELECT 1 FROM plan_steps p "
    "WHERE p.workspace_id = t.workspace_id AND p.task_id = t.task_id "
    "AND p.valid_to IS NULL) "
    "ORDER BY t.updated_at DESC LIMIT 1"
)


def _cap_words(text: str, limit: int) -> str:
    """Collapse whitespace and cap ``text`` to ``limit`` words.

    Collapsing is structural: a newline inside a title or body would
    otherwise split one brief line into several and break the section's
    one-record-per-line layout.
    """
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip(".,;:") + "..."


def _render_lines(task_id: str, goal: str, steps: list[PlanStep]) -> list[str]:
    """Compact plan render: header + done count, the in-progress step,
    any blocked steps, then the next pending step titles.

    Only the first ``active`` step (by rank) is rendered. The schema
    permits several, but the agent is doing one thing at a time; extra
    in-progress lines would only crowd the count and next-step lines.
    """
    # Skipped steps are off-path -- exclude them from the count so
    # "N/M done" reflects the work that actually remains.
    total = sum(1 for s in steps if s.status != "skipped")
    done = sum(1 for s in steps if s.status == "done")
    lines = [
        "## Active plan",
        f"task {task_id}: {_cap_words(goal, _GOAL_WORDS)} ({done}/{total} done)",
    ]
    active = next((s for s in steps if s.status == "active"), None)
    if active is not None:
        lines.append(f"→ doing: {_cap_words(active.title, _TITLE_WORDS)}")
        body = _cap_words(active.body, _BODY_WORDS)
        if body:
            lines.append(f"  {body}")
    for step in steps:
        if step.status == "blocked":
            lines.append(f"blocked: {_cap_words(step.title, _TITLE_WORDS)}")
    pending = [s for s in steps if s.status == "pending"]
    for step in pending[:_NEXT_STEPS_SHOWN]:
        lines.append(f"next: {_cap_words(step.title, _TITLE_WORDS)}")
    return lines


def _active_plan_lines(conn: sqlite3.Connection, workspace_id: str, budget: int) -> list[str]:
    """Fitted plan lines, or ``[]`` when there is nothing worth pinning.

    Empty when: no in-progress task owns live plan steps; the table is
    absent (pre-migration DB); every live step is skipped (a "0/0 done"
    header is pure noise); or the budget is too tight to keep the
    in-progress line (a header with the current step missing misleads
    more than it informs -- the freed budget is then redistributed).
    """
    try:
        row = conn.execute(_PLAN_TASK_SQL, (workspace_id,)).fetchone()
    except sqlite3.OperationalError:
        return []  # tasks / plan_steps table absent (pre-migration DB)
    if row is None or not row["task_id"]:
        return []
    task_id = str(row["task_id"])
    steps = list_plan_steps(conn, workspace_id, task_id)
    if not any(s.status != "skipped" for s in steps):
        return []
    lines = fit_to_budget(_render_lines(task_id, row["goal_one_line"] or "?", steps), budget)
    # Invariant: a rendered plan must show the in-progress step. If the
    # budget dropped it (or only the header survived), drop the section.
    has_active = any(s.status == "active" for s in steps)
    shows_doing = any(line.startswith("→ doing:") for line in lines)
    if len(lines) < 2 or (has_active and not shows_doing):
        return []
    return lines


def _build_active_plan(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Compact pinned view of an in-progress task's plan.

    Picks the most-recently-updated in-progress task that has live plan
    steps. Empty when no such task exists — compose_brief redistributes
    the freed budget.
    """
    return BriefSection(
        name="active_plan",
        budget=budget,
        lines=_active_plan_lines(conn, workspace_id, budget),
    )
