from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.task_state_writer import write_task_state
from agent_memory_lite.models.task_state import TaskStateIn
from agent_memory_lite.repositories.task_state_repo import (
    get_task_state,
    list_active_task_states,
)


def _state(**overrides: object) -> TaskStateIn:
    payload: dict[str, object] = {
        "workspace_id": "default",
        "task_id": "memory-lite",
        "goal": "ship Phase 3",
        "status": "in_progress",
        "current_plan": ["models", "writers", "extraction"],
        "completed_steps": ["models"],
        "next_action": "writers",
    }
    payload.update(overrides)
    return TaskStateIn(**payload)


def test_initial_write_creates_row(applied_conn: sqlite3.Connection) -> None:
    state = write_task_state(applied_conn, _state())
    assert state.task_id == "memory-lite"
    assert state.next_action == "writers"
    assert state.id.startswith("task_")


def test_subsequent_write_updates_in_place(applied_conn: sqlite3.Connection) -> None:
    first = write_task_state(applied_conn, _state())
    second = write_task_state(
        applied_conn,
        _state(
            status="done", next_action=None, completed_steps=["models", "writers", "extraction"]
        ),
    )
    assert first.id == second.id
    assert second.status == "done"
    assert second.next_action is None


def test_active_filter_drops_done(applied_conn: sqlite3.Connection) -> None:
    write_task_state(applied_conn, _state(status="done"))
    write_task_state(
        applied_conn,
        _state(task_id="other-task", status="in_progress"),
    )
    actives = list_active_task_states(applied_conn, "default")
    assert {s.task_id for s in actives} == {"other-task"}


def test_get_task_state_returns_latest(applied_conn: sqlite3.Connection) -> None:
    write_task_state(applied_conn, _state(status="in_progress"))
    write_task_state(applied_conn, _state(status="blocked", blockers=["b1"]))
    state = get_task_state(applied_conn, "default", "memory-lite")
    assert state is not None
    assert state.status == "blocked"
    assert state.blockers == ["b1"]
