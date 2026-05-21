"""Unit tests for repositories/plan_step_repo.py.

These also exercise migration 0038 — the `applied_conn` fixture applies
every root migration, so a SQL error in 0038_plan_steps.sql fails here.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.models.plan_step import PlanStep
from agent_memory_lite.repositories.plan_step_repo import (
    get_plan_step,
    insert_plan_step_row,
    list_plan_steps,
    max_rank,
    update_plan_step_row,
)


def _insert_step(
    conn: sqlite3.Connection,
    *,
    step_id: str,
    rank: float,
    task_id: str = "t1",
    status: str = "pending",
) -> None:
    insert_plan_step_row(
        conn,
        step_id=step_id,
        workspace_id="ws",
        task_id=task_id,
        title=f"step {step_id}",
        body="",
        status=status,
        parent_step_id=None,
        rank=rank,
        supersedes_step_id=None,
        source_episode_id=None,
        valid_from="2026-05-21T00:00:00Z",
        timestamp="2026-05-21T00:00:00Z",
    )


def test_insert_and_get(applied_conn: sqlite3.Connection) -> None:
    _insert_step(applied_conn, step_id="ps1", rank=1.0, status="active")
    step = get_plan_step(applied_conn, "ws", "ps1")
    assert isinstance(step, PlanStep)
    assert step.title == "step ps1"
    assert step.status == "active"
    assert step.rank == 1.0
    assert step.valid_to is None


def test_get_missing_returns_none(applied_conn: sqlite3.Connection) -> None:
    assert get_plan_step(applied_conn, "ws", "nope") is None


def test_list_ordered_by_rank(applied_conn: sqlite3.Connection) -> None:
    _insert_step(applied_conn, step_id="b", rank=2.0)
    _insert_step(applied_conn, step_id="a", rank=1.0)
    _insert_step(applied_conn, step_id="c", rank=3.0)
    steps = list_plan_steps(applied_conn, "ws", "t1")
    assert [s.id for s in steps] == ["a", "b", "c"]


def test_list_excludes_removed_steps(applied_conn: sqlite3.Connection) -> None:
    """A step removed in a re-plan (valid_to set) drops from the active
    view but stays reachable via include_removed."""
    _insert_step(applied_conn, step_id="keep", rank=1.0)
    _insert_step(applied_conn, step_id="gone", rank=2.0)
    update_plan_step_row(
        applied_conn,
        workspace_id="ws",
        step_id="gone",
        title="step gone",
        body="",
        status="skipped",
        parent_step_id=None,
        rank=2.0,
        supersedes_step_id=None,
        valid_to="2026-05-21T01:00:00Z",
        timestamp="2026-05-21T01:00:00Z",
    )
    assert [s.id for s in list_plan_steps(applied_conn, "ws", "t1")] == ["keep"]
    assert [s.id for s in list_plan_steps(applied_conn, "ws", "t1", include_removed=True)] == [
        "keep",
        "gone",
    ]


def test_update_status(applied_conn: sqlite3.Connection) -> None:
    _insert_step(applied_conn, step_id="ps1", rank=1.0, status="pending")
    update_plan_step_row(
        applied_conn,
        workspace_id="ws",
        step_id="ps1",
        title="step ps1",
        body="",
        status="done",
        parent_step_id=None,
        rank=1.0,
        supersedes_step_id=None,
        valid_to=None,
        timestamp="2026-05-21T02:00:00Z",
    )
    step = get_plan_step(applied_conn, "ws", "ps1")
    assert step is not None
    assert step.status == "done"
    assert step.updated_at == "2026-05-21T02:00:00Z"


def test_max_rank(applied_conn: sqlite3.Connection) -> None:
    assert max_rank(applied_conn, "ws", "t1") is None
    _insert_step(applied_conn, step_id="a", rank=1.0)
    _insert_step(applied_conn, step_id="b", rank=4.5)
    assert max_rank(applied_conn, "ws", "t1") == 4.5
