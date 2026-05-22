"""Brain-pass step: distil a completed plan into a replayable playbook.

Phase 5b of the plan-storage redesign. Once every live step of a plan
(one ``task_id``) is ``done`` or ``skipped`` -- and at least one is
``done`` -- the plan describes a workflow that actually ran. This step
captures it as an ``agent_playbook`` whose steps are the done-step
titles in rank order, so a future agent retrieves the recipe instead of
re-deriving it from raw episodes.

Idempotent: the playbook name is ``plan:<task_id>``, so a plan that was
already distilled is skipped (its playbook already exists). Failure-soft
and capped per pass, like every brain-pass loop.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.capability_writer import upsert_agent_playbook
from agent_memory_lite.models.capabilities import AgentPlaybookIn
from agent_memory_lite.models.plan_step import PlanStep
from agent_memory_lite.repositories.capabilities_repo import get_playbook_by_name
from agent_memory_lite.repositories.plan_step_repo import list_plan_steps

# Auto-distilled, not human-authored and not yet validated by reuse --
# a notch below the AgentPlaybookIn default (0.7).
_DISTILLED_CONFIDENCE = 0.6


def _distinct_task_ids(conn: sqlite3.Connection, workspace_id: str) -> list[str]:
    """task_ids with at least one live (not re-planned-out) plan step.

    Failure-soft: a missing ``plan_steps`` table returns ``[]``.
    """
    try:
        cursor = conn.execute(
            "SELECT DISTINCT task_id FROM plan_steps WHERE workspace_id = ? AND valid_to IS NULL",
            (workspace_id,),
        )
    except sqlite3.OperationalError:
        return []
    return [str(row[0]) for row in cursor.fetchall()]


def _is_complete(steps: list[PlanStep]) -> bool:
    """True when a plan is finished and worth distilling.

    Finished = there are live steps and every one is ``done`` or
    ``skipped``. Worth distilling = at least one is ``done`` -- an
    all-skipped plan was abandoned, not executed.
    """
    if not steps:
        return False
    if any(step.status not in ("done", "skipped") for step in steps):
        return False
    return any(step.status == "done" for step in steps)


def _build_playbook(workspace_id: str, task_id: str, steps: list[PlanStep]) -> AgentPlaybookIn:
    """Compose the playbook from the plan's done-step titles in rank order.

    ``steps`` arrives rank-ordered from ``list_plan_steps``; skipped steps
    are detours not taken, so only the ``done`` titles become the recipe.
    """
    return AgentPlaybookIn(
        workspace_id=workspace_id,
        name=f"plan:{task_id}",
        goal=f"Replay of completed plan '{task_id}'.",
        steps=[step.title for step in steps if step.status == "done"],
        confidence=_DISTILLED_CONFIDENCE,
        active=True,
    )


def distill_completed_plans(
    conn: sqlite3.Connection, *, workspace_id: str, max_distill: int
) -> int:
    """Create a ``plan:<task_id>`` playbook for each completed plan.

    Idempotent: a plan whose playbook already exists is skipped and does
    not count against ``max_distill`` -- the cap bounds only NEW
    playbooks per pass. Returns the number of playbooks created.
    """
    distilled = 0
    for task_id in _distinct_task_ids(conn, workspace_id):
        if distilled >= max_distill:
            break
        steps = list_plan_steps(conn, workspace_id, task_id)
        if not _is_complete(steps):
            continue
        name = f"plan:{task_id}"
        if get_playbook_by_name(conn, workspace_id=workspace_id, name=name) is not None:
            continue
        upsert_agent_playbook(conn, _build_playbook(workspace_id, task_id, steps))
        distilled += 1
    return distilled
