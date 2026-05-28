"""Brief section builder — the workspace's active plan.

Phase 3 of the plan-storage redesign. The plan is pinned into the brief
but rendered compact: goal + done/total count, the step in progress, any
blocked steps, the next pending step titles. The full plan and per-step
detail stay fetch-on-demand via memory_get / memory_search.

Phase 4 adds step-enter skill activation: capabilities (skills / roles /
playbooks) linked to the in-progress step via capability_links surface
as an ``apply:`` line, so the agent applies the step's bound skill
rather than just reading the plan.

Phase 5a adds a ``review:`` line: heuristic plan-health nudges (more
than one step active, no active step, blocked work) so memory flags a
plan that has drifted off the rails.

Free text is word-capped so a verbose title or body cannot crowd out the
essential in-progress line; when even the capped in-progress line will
not fit the budget, the whole section is dropped (its budget is
redistributed) rather than left as a misleading contentless header.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.cognition.brief_models import BriefSection
from agent_memory_lite.cognition.brief_plan_review import _plan_review
from agent_memory_lite.cognition.brief_tokens import fit_to_budget
from agent_memory_lite.models.enums import CapabilityLinkTargetType
from agent_memory_lite.models.plan_step import PlanStep
from agent_memory_lite.repositories.capability_links_repo import list_capability_links
from agent_memory_lite.repositories.plan_step_repo import list_plan_steps

_NEXT_STEPS_SHOWN = 2
# How many capabilities (skills / roles / playbooks) bound to the
# in-progress step are surfaced on the brief's "apply:" line.
_APPLY_CAPS_SHOWN = 3

# Word caps for free text. The active-plan budget is tight (~35 tokens at
# the default 500-token brief), so an uncapped title / body / goal would
# let one verbose step push the in-progress line out of the section.
_GOAL_WORDS = 10
_TITLE_WORDS = 10
_BODY_WORDS = 16
_APPLY_WORDS = 14

# The most-recently-updated open task that owns at least one live
# plan step. The EXISTS sub-query filters BEFORE the LIMIT, so the
# plan-bearing task is found no matter how many plan-less open
# tasks happen to have been touched more recently.
_PLAN_TASK_SQL = (
    "SELECT t.task_id AS task_id, t.goal_one_line AS goal_one_line FROM tasks t "
    "WHERE t.workspace_id = ? AND t.status IN ('active', 'in_progress') "
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


def _active_step_capabilities(
    conn: sqlite3.Connection, workspace_id: str, step: PlanStep
) -> list[str]:
    """Names of the capabilities bound to the in-progress step.

    Phase 4 step-enter activation: skills / roles / playbooks linked to
    the active step via capability_links. Strongest binding first
    (``list_capability_links`` orders by strength). Empty when nothing
    is bound or the table is absent (pre-migration DB).
    """
    try:
        links = list_capability_links(
            conn,
            workspace_id=workspace_id,
            target_type=CapabilityLinkTargetType.PLAN_STEP,
            target_id=step.id,
            limit=_APPLY_CAPS_SHOWN,
        )
    except sqlite3.OperationalError:
        return []
    return [link.capability_name for link in links]


def _render_lines(
    task_id: str, goal: str, steps: list[PlanStep], active_caps: list[str]
) -> list[str]:
    """Compact plan render: header + done count, the in-progress step
    (with its bound capabilities), a plan-review nudge, any blocked
    steps, then the next pending step titles.

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
    # Phase 5a: plan-health nudges sit right under the in-progress line
    # (or the count line when no step is active). fit_to_budget keeps
    # lines in list order, so a line placed AFTER the doing line cannot
    # affect whether the doing line fits -- the nudge competes only with
    # the apply / body / next detail below it, never with the essential
    # in-progress line.
    review = _plan_review(steps)
    if review:
        lines.append(f"review: {'; '.join(review)}")
    if active is not None:
        # Phase 4: bound skills/roles surface right under the step so the
        # agent applies them. Placed before the body -- "how to do this
        # step" outranks the step's own descriptive detail under budget.
        if active_caps:
            # Join with " · ", not ", ": a capability name is free text
            # and a comma in one would blur into the separator.
            lines.append(f"  apply: {_cap_words(' · '.join(active_caps), _APPLY_WORDS)}")
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

    Empty when: no open task owns live plan steps; the table is
    absent (pre-migration DB); every live step is skipped (a "0/0 done"
    header is pure noise); or the budget is too tight to keep the count
    line or the in-progress line (a header with the plan's identity or
    current step missing misleads more than it informs -- the freed
    budget is then redistributed).
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
    active = next((s for s in steps if s.status == "active"), None)
    active_caps = _active_step_capabilities(conn, workspace_id, active) if active else []
    rendered = _render_lines(task_id, row["goal_one_line"] or "?", steps, active_caps)
    lines = fit_to_budget(rendered, budget)
    # Invariant: a rendered plan must keep its identity (the count line)
    # and, when a step is active, the in-progress line. If the budget
    # dropped either, drop the section -- a header plus a stray nudge or
    # next line is not worth pinning.
    shows_count = any(line.startswith("task ") for line in lines)
    shows_doing = any(line.startswith("→ doing:") for line in lines)
    if not shows_count or (active is not None and not shows_doing):
        return []
    return lines


def _build_active_plan(conn: sqlite3.Connection, workspace_id: str, budget: int) -> BriefSection:
    """Compact pinned view of an open task's plan.

    Picks the most-recently-updated open task that has live plan
    steps. Empty when no such task exists — compose_brief redistributes
    the freed budget.
    """
    return BriefSection(
        name="active_plan",
        budget=budget,
        lines=_active_plan_lines(conn, workspace_id, budget),
    )
