"""Business-layer writes for plan steps.

Wraps the generic versioned writer (``storage/writer.py``) with the one
piece of plan semantics the generic writer lacks: ``rank`` assignment on
append. Every mutation rides writer.py, so each write is snapshotted
into the ``versions`` table and appended to ``audit_log`` — history
comes free.

The compact ``memory_write`` surface (HTTP ``/memory/write`` and the MCP
``memory_write`` handler) routes ``kind="plan_step"`` through
``add_plan_step_from_payload`` here, NOT through the generic writer:
the generic writer has no ``rank`` default and the INSERT fails the
NOT NULL constraint on ``plan_steps.rank``. Plain status / rank /
valid_to edits keep going through the generic ``memory_edit`` — they
are ordinary column updates and need no plan-specific wrapper.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.models.plan_step import PlanStepIn
from agent_memory_lite.repositories.plan_step_repo import max_rank
from agent_memory_lite.storage import writer

_RANK_GAP = 1.0


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


def add_plan_step_from_payload(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    payload: dict[str, Any],
    agent_id: str = "agent",
    source_episode_id: str | None = None,
) -> dict[str, Any] | None:
    """Append a plan step from a raw ``memory_write`` payload dict.

    The compact ``memory_write`` surface hands plan-step fields as a
    loose dict. This is the single adapter the HTTP route and the MCP
    handler both call, so a plan-step create always flows through
    ``add_plan_step`` (which assigns ``rank``) instead of the generic
    writer, which has no rank default and would reject the INSERT with
    a NOT NULL violation on ``plan_steps.rank``.

    Raises ``pydantic.ValidationError`` when ``payload`` is missing a
    required field (``task_id`` / ``title``) or carries an unknown one
    (``PlanStepIn`` is ``extra="forbid"``); the caller maps that to an
    ``invalid_args`` envelope.
    """
    fields = {k: v for k, v in payload.items() if k != "workspace_id"}
    if source_episode_id is not None:
        fields.setdefault("source_episode_id", source_episode_id)
    step_in = PlanStepIn(workspace_id=workspace_id, **fields)
    return add_plan_step(conn, step_in=step_in, agent_id=agent_id)
