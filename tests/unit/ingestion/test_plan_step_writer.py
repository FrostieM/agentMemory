"""Unit tests for ingestion/plan_step_writer.py.

Built on the canonical v3 schema — it carries the ``versions`` and
``audit_log`` tables the writer snapshots into, which the root-migration
fixture (``applied_conn``) does not. Same fixture style as test_writer.py.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.ingestion.plan_step_writer import (
    add_plan_step,
    move_plan_step,
    remove_plan_step,
    set_plan_step_status,
)
from agent_memory_lite.models.plan_step import PlanStepIn
from agent_memory_lite.repositories.plan_step_repo import get_plan_step, list_plan_steps
from agent_memory_lite.storage import writer

_SCHEMA = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _add(conn: sqlite3.Connection, *, title: str, rank: float | None = None) -> str:
    out = add_plan_step(
        conn,
        step_in=PlanStepIn(workspace_id="ws", task_id="t1", title=title, rank=rank),
    )
    assert out is not None
    return str(out["id"])


def test_add_plan_step_auto_rank(conn: sqlite3.Connection) -> None:
    """Rank unset -> the step is appended after the current last step."""
    first = _add(conn, title="first")
    second = _add(conn, title="second")
    steps = list_plan_steps(conn, "ws", "t1")
    assert [s.id for s in steps] == [first, second]
    assert steps[0].rank < steps[1].rank


def test_add_plan_step_explicit_rank(conn: sqlite3.Connection) -> None:
    sid = _add(conn, title="only", rank=7.0)
    step = get_plan_step(conn, "ws", sid)
    assert step is not None
    assert step.rank == 7.0
    assert step.valid_from is not None  # writer.py defaults it on write


def test_set_plan_step_status(conn: sqlite3.Connection) -> None:
    sid = _add(conn, title="step")
    set_plan_step_status(conn, workspace_id="ws", step_id=sid, status="active")
    step = get_plan_step(conn, "ws", sid)
    assert step is not None
    assert step.status == "active"


def test_move_plan_step(conn: sqlite3.Connection) -> None:
    a = _add(conn, title="a", rank=1.0)
    b = _add(conn, title="b", rank=2.0)
    move_plan_step(conn, workspace_id="ws", step_id=a, rank=3.0)
    steps = list_plan_steps(conn, "ws", "t1")
    assert [s.id for s in steps] == [b, a]


def test_remove_plan_step(conn: sqlite3.Connection) -> None:
    """remove stamps valid_to -> the step drops from the active plan."""
    keep = _add(conn, title="keep")
    gone = _add(conn, title="gone")
    remove_plan_step(conn, workspace_id="ws", step_id=gone)
    active = list_plan_steps(conn, "ws", "t1")
    assert [s.id for s in active] == [keep]
    all_steps = list_plan_steps(conn, "ws", "t1", include_removed=True)
    assert {s.id for s in all_steps} == {keep, gone}


def test_writes_are_versioned(conn: sqlite3.Connection) -> None:
    """Every mutation snapshots the prior row into the versions table."""
    sid = _add(conn, title="step")
    set_plan_step_status(conn, workspace_id="ws", step_id=sid, status="active")
    set_plan_step_status(conn, workspace_id="ws", step_id=sid, status="done")
    versions = writer.list_versions(conn, workspace_id="ws", kind="plan_step", object_id=sid)
    assert len(versions) >= 2


def test_edit_ops_on_missing_step_return_none(conn: sqlite3.Connection) -> None:
    """set / move / remove on an unknown step id are no-ops returning None."""
    assert set_plan_step_status(conn, workspace_id="ws", step_id="nope", status="done") is None
    assert move_plan_step(conn, workspace_id="ws", step_id="nope", rank=1.0) is None
    assert remove_plan_step(conn, workspace_id="ws", step_id="nope") is None


def test_add_plan_step_with_parent(conn: sqlite3.Connection) -> None:
    """A sub-step carries its parent_step_id through the writer."""
    root = _add(conn, title="root")
    out = add_plan_step(
        conn,
        step_in=PlanStepIn(workspace_id="ws", task_id="t1", title="child", parent_step_id=root),
    )
    assert out is not None
    assert out["parent_step_id"] == root
