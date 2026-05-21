"""SQL operations for the `plan_steps` table.

Thin SQL wrappers only — id minting, rank assignment, and version/audit
history are layered on by the plan-step writer, not here.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.plan_step import PlanStep, PlanStepStatus


def _row_to_plan_step(row: sqlite3.Row) -> PlanStep:
    return PlanStep(
        id=row["id"],
        workspace_id=row["workspace_id"],
        task_id=row["task_id"],
        title=row["title"],
        body=row["body"],
        status=row["status"],
        parent_step_id=row["parent_step_id"],
        rank=row["rank"],
        supersedes_step_id=row["supersedes_step_id"],
        source_episode_id=row["source_episode_id"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert_plan_step_row(
    conn: sqlite3.Connection,
    *,
    step_id: str,
    workspace_id: str,
    task_id: str,
    title: str,
    body: str,
    status: PlanStepStatus,
    parent_step_id: str | None,
    rank: float,
    supersedes_step_id: str | None,
    source_episode_id: str | None,
    valid_from: str,
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO plan_steps (
            id, workspace_id, task_id, parent_step_id, rank, title, body,
            status, supersedes_step_id, source_episode_id, valid_from,
            valid_to, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            step_id,
            workspace_id,
            task_id,
            parent_step_id,
            rank,
            title,
            body,
            status,
            supersedes_step_id,
            source_episode_id,
            valid_from,
            timestamp,
            timestamp,
        ),
    )


def get_plan_step(conn: sqlite3.Connection, workspace_id: str, step_id: str) -> PlanStep | None:
    row = conn.execute(
        "SELECT * FROM plan_steps WHERE workspace_id = ? AND id = ?",
        (workspace_id, step_id),
    ).fetchone()
    return _row_to_plan_step(row) if row is not None else None


def list_plan_steps(
    conn: sqlite3.Connection,
    workspace_id: str,
    task_id: str,
    *,
    include_removed: bool = False,
) -> list[PlanStep]:
    """Steps of one plan, ordered by rank. Steps removed in a re-plan
    (``valid_to`` set) are excluded unless ``include_removed`` is True."""
    sql = "SELECT * FROM plan_steps WHERE workspace_id = ? AND task_id = ?"
    if not include_removed:
        sql += " AND valid_to IS NULL"
    sql += " ORDER BY rank"
    rows = conn.execute(sql, (workspace_id, task_id)).fetchall()
    return [_row_to_plan_step(r) for r in rows]


def update_plan_step_row(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    step_id: str,
    title: str,
    body: str,
    status: PlanStepStatus,
    parent_step_id: str | None,
    rank: float,
    supersedes_step_id: str | None,
    valid_to: str | None,
    timestamp: str,
) -> None:
    conn.execute(
        """
        UPDATE plan_steps SET
            title = ?, body = ?, status = ?, parent_step_id = ?, rank = ?,
            supersedes_step_id = ?, valid_to = ?, updated_at = ?
        WHERE workspace_id = ? AND id = ?
        """,
        (
            title,
            body,
            status,
            parent_step_id,
            rank,
            supersedes_step_id,
            valid_to,
            timestamp,
            workspace_id,
            step_id,
        ),
    )


def max_rank(conn: sqlite3.Connection, workspace_id: str, task_id: str) -> float | None:
    """Highest rank among a plan's steps, or None when the plan has none."""
    row = conn.execute(
        "SELECT MAX(rank) AS m FROM plan_steps WHERE workspace_id = ? AND task_id = ?",
        (workspace_id, task_id),
    ).fetchone()
    # MAX() always yields one row — m is NULL when the plan has no steps.
    highest: float | None = row["m"]
    return highest
