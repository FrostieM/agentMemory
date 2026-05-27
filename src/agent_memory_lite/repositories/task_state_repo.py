"""SQL operations for the canonical `tasks` table.

Each (workspace_id, task_id) pair has at most one row. Writes are upserts.
"""

from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.models.task_state import TaskState


def _row_to_task(row: sqlite3.Row) -> TaskState:
    return TaskState(
        id=row["id"],
        workspace_id=row["workspace_id"],
        task_id=row["task_id"],
        goal=row["goal"],
        status=row["status"],
        current_plan=json.loads(row["current_plan_json"] or "[]"),
        completed_steps=json.loads(row["completed_steps_json"] or "[]"),
        next_action=row["next_action"],
        blockers=json.loads(row["blockers_json"] or "[]"),
        files_in_scope=json.loads(row["files_in_scope_json"] or "[]"),
        source_episode_id=row["source_episode_id"],
        updated_at=row["updated_at"],
    )


def upsert_task_state_row(
    conn: sqlite3.Connection,
    *,
    state_id: str,
    workspace_id: str,
    task_id: str,
    goal: str,
    status: str,
    current_plan: list[str],
    completed_steps: list[str],
    next_action: str | None,
    blockers: list[str],
    files_in_scope: list[str],
    source_episode_id: str | None,
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO tasks (
            id, workspace_id, task_id, goal, goal_one_line, status,
            current_plan_json, completed_steps_json, next_action,
            blockers_json, files_in_scope_json, source_episode_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, task_id) DO UPDATE SET
            goal = excluded.goal,
            goal_one_line = excluded.goal_one_line,
            status = excluded.status,
            current_plan_json = excluded.current_plan_json,
            completed_steps_json = excluded.completed_steps_json,
            next_action = excluded.next_action,
            blockers_json = excluded.blockers_json,
            files_in_scope_json = excluded.files_in_scope_json,
            source_episode_id = excluded.source_episode_id,
            updated_at = excluded.updated_at
        """,
        (
            state_id,
            workspace_id,
            task_id,
            goal,
            goal,
            status,
            json.dumps(current_plan, sort_keys=True),
            json.dumps(completed_steps, sort_keys=True),
            next_action,
            json.dumps(blockers, sort_keys=True),
            json.dumps(files_in_scope, sort_keys=True),
            source_episode_id,
            timestamp,
        ),
    )


def get_task_state(conn: sqlite3.Connection, workspace_id: str, task_id: str) -> TaskState | None:
    row = conn.execute(
        "SELECT * FROM tasks WHERE workspace_id = ? AND task_id = ?",
        (workspace_id, task_id),
    ).fetchone()
    return _row_to_task(row) if row is not None else None


def list_active_task_states(conn: sqlite3.Connection, workspace_id: str) -> list[TaskState]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
        WHERE workspace_id = ? AND status NOT IN ('done', 'cancelled', 'archived')
        ORDER BY updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [_row_to_task(r) for r in rows]
