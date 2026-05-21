"""Business-layer writes for plan steps.

Wraps the generic versioned writer (``storage/writer.py``) with plan
semantics: rank assignment on append, status transitions, reorder, and
re-plan removal. Every mutation here is snapshotted into the ``versions``
table and appended to ``audit_log`` by writer.py — history comes free.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from agent_memory_lite.models.plan_step import PlanStepIn, PlanStepStatus
from agent_memory_lite.repositories.plan_step_repo import max_rank
from agent_memory_lite.storage import writer

_RANK_GAP = 1.0


def _utc_now() -> str:
    """ISO-8601 UTC with a ``Z`` suffix — matches the format storage/writer.py
    stamps on valid_from / created_at / updated_at, so a plan step's
    bi-temporal columns stay lexicographically comparable."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def add_plan_step(
    conn: sqlite3.Connection,
    *,
    step_in: PlanStepIn,
    agent_id: str = "agent",
) -> dict[str, Any] | None:
    """Append (or insert) a plan step. When ``rank`` is unset the step is
    placed after the plan's current last step."""
    rank = step_in.rank
    if rank is None:
        current_max = max_rank(conn, step_in.workspace_id, step_in.task_id)
        rank = _RANK_GAP if current_max is None else current_max + _RANK_GAP
    payload: dict[str, Any] = {
        "task_id": step_in.task_id,
        "title": step_in.title,
        "body": step_in.body,
        "status": step_in.status,
        "parent_step_id": step_in.parent_step_id,
        "rank": rank,
        "supersedes_step_id": step_in.supersedes_step_id,
        "source_episode_id": step_in.source_episode_id,
    }
    return writer.write(
        conn,
        workspace_id=step_in.workspace_id,
        kind="plan_step",
        payload=payload,
        agent_id=agent_id,
        source_episode_id=step_in.source_episode_id,
    )


def set_plan_step_status(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    step_id: str,
    status: PlanStepStatus,
    agent_id: str = "agent",
) -> dict[str, Any] | None:
    """Transition one step's status (pending / active / done / blocked / skipped)."""
    return writer.edit(
        conn,
        workspace_id=workspace_id,
        kind="plan_step",
        object_id=step_id,
        fields={"status": status},
        agent_id=agent_id,
    )


def move_plan_step(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    step_id: str,
    rank: float,
    agent_id: str = "agent",
) -> dict[str, Any] | None:
    """Reorder a step by assigning a new rank — use a midpoint to slot it
    between two existing steps without renumbering the rest."""
    return writer.edit(
        conn,
        workspace_id=workspace_id,
        kind="plan_step",
        object_id=step_id,
        fields={"rank": rank},
        agent_id=agent_id,
    )


def remove_plan_step(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    step_id: str,
    agent_id: str = "agent",
) -> dict[str, Any] | None:
    """Remove a step from the active plan in a re-plan: stamps ``valid_to``
    so the step drops from the current view but stays in the trajectory."""
    return writer.edit(
        conn,
        workspace_id=workspace_id,
        kind="plan_step",
        object_id=step_id,
        fields={"valid_to": _utc_now()},
        agent_id=agent_id,
    )
