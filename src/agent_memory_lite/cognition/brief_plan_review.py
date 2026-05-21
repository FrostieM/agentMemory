"""Heuristic plan-health review — Phase 5a of the plan-storage redesign.

A pure function over a task's live plan steps that flags a plan the
agent has drifted on. The brief's active-plan section renders the nudges
as a compact ``review:`` line. Kept in its own module so the heuristic
set can grow without pushing brief_sections_plan.py over the SLOC
ceiling.
"""

from __future__ import annotations

from agent_memory_lite.models.plan_step import PlanStep


def _plan_review(steps: list[PlanStep]) -> list[str]:
    """Plan-health nudges: more than one step active at once, no active
    step while work is pending (the place was lost), or blocked steps.
    Empty when the plan is healthy.
    """
    active = sum(1 for s in steps if s.status == "active")
    pending = sum(1 for s in steps if s.status == "pending")
    blocked = sum(1 for s in steps if s.status == "blocked")
    nudges: list[str] = []
    if active > 1:
        nudges.append(f"{active} steps active, finish one first")
    elif active == 0 and pending:
        nudges.append(f"no active step, {pending} pending")
    if blocked:
        nudges.append(f"{blocked} blocked")
    return nudges
