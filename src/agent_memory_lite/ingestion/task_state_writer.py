"""Upsert a task state row.

`(workspace_id, task_id)` uniquely identifies a row; subsequent writes update
the existing row in place. The previous state is captured in the audit log.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.db.transactions import with_tx
from agent_memory_lite.models.task_state import TaskState, TaskStateIn
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.repositories.task_state_repo import (
    get_task_state,
    upsert_task_state_row,
)
from agent_memory_lite.utils.ids import IdKind, new_id
from agent_memory_lite.utils.time import iso_now


def write_task_state(conn: sqlite3.Connection, payload: TaskStateIn) -> TaskState:
    timestamp = iso_now()
    existing = get_task_state(conn, payload.workspace_id, payload.task_id)
    state_id = existing.id if existing is not None else new_id(IdKind.TASK_STATE)

    with with_tx(conn):
        upsert_task_state_row(
            conn,
            state_id=state_id,
            workspace_id=payload.workspace_id,
            task_id=payload.task_id,
            goal=payload.goal,
            status=payload.status,
            current_plan=payload.current_plan,
            completed_steps=payload.completed_steps,
            next_action=payload.next_action,
            blockers=payload.blockers,
            files_in_scope=payload.files_in_scope,
            source_episode_id=payload.source_episode_id,
            timestamp=timestamp,
        )
        insert_audit(
            conn,
            workspace_id=payload.workspace_id,
            action="update_task_state",
            target_type="task_state",
            target_id=state_id,
            source_episode_id=payload.source_episode_id,
            before={"task_id": payload.task_id, "status": existing.status} if existing else None,
            after={"task_id": payload.task_id, "status": payload.status},
        )

    state = get_task_state(conn, payload.workspace_id, payload.task_id)
    assert state is not None
    return state
