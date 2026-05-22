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
from pydantic import ValidationError

from agent_memory_lite.ingestion.plan_step_writer import (
    add_plan_step,
    add_plan_step_from_payload,
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


def test_add_plan_step_with_parent(conn: sqlite3.Connection) -> None:
    """A sub-step carries its parent_step_id through the writer."""
    root = _add(conn, title="root")
    out = add_plan_step(
        conn,
        step_in=PlanStepIn(workspace_id="ws", task_id="t1", title="child", parent_step_id=root),
    )
    assert out is not None
    assert out["parent_step_id"] == root


def test_writes_are_versioned(conn: sqlite3.Connection) -> None:
    """Every mutation snapshots the prior row into the versions table.

    Status edits go through the generic ``writer.edit`` (``memory_edit``)
    — plan_step_writer no longer carries a status wrapper.
    """
    sid = _add(conn, title="step")
    writer.edit(
        conn, workspace_id="ws", kind="plan_step", object_id=sid, fields={"status": "active"}
    )
    writer.edit(conn, workspace_id="ws", kind="plan_step", object_id=sid, fields={"status": "done"})
    versions = writer.list_versions(conn, workspace_id="ws", kind="plan_step", object_id=sid)
    assert len(versions) >= 2


def test_add_from_payload_auto_ranks(conn: sqlite3.Connection) -> None:
    """The memory_write adapter mints rank when the payload omits it —
    the generic writer would fail the INSERT on the NOT NULL rank column."""
    first = add_plan_step_from_payload(
        conn, workspace_id="ws", payload={"task_id": "t1", "title": "first"}
    )
    second = add_plan_step_from_payload(
        conn, workspace_id="ws", payload={"task_id": "t1", "title": "second"}
    )
    assert first is not None
    assert second is not None
    steps = list_plan_steps(conn, "ws", "t1")
    assert [s.id for s in steps] == [first["id"], second["id"]]
    assert steps[0].rank < steps[1].rank


def test_add_from_payload_honours_explicit_rank(conn: sqlite3.Connection) -> None:
    """An explicit rank in the payload is passed straight through."""
    out = add_plan_step_from_payload(
        conn, workspace_id="ws", payload={"task_id": "t1", "title": "only", "rank": 5.0}
    )
    assert out is not None
    assert out["rank"] == 5.0


def test_add_from_payload_rejects_missing_required_field(conn: sqlite3.Connection) -> None:
    """A payload missing a required field (title) raises ValidationError —
    the HTTP route / MCP handler map that to an invalid_args envelope."""
    with pytest.raises(ValidationError):
        add_plan_step_from_payload(conn, workspace_id="ws", payload={"task_id": "t1"})


def test_add_from_payload_rejects_unknown_field(conn: sqlite3.Connection) -> None:
    """PlanStepIn is extra='forbid' — an unknown payload key (e.g. a
    caller-supplied id, which a plan-step create does not accept) is
    rejected rather than silently dropped."""
    with pytest.raises(ValidationError):
        add_plan_step_from_payload(
            conn,
            workspace_id="ws",
            payload={"task_id": "t1", "title": "x", "id": "pstep_x"},
        )


def test_rank_edit_reorders_via_generic_writer(conn: sqlite3.Connection) -> None:
    """A rank edit through the generic writer (memory_edit) reorders the
    plan — the behaviour the deleted move_plan_step wrapper used to lock."""
    a = _add(conn, title="a", rank=1.0)
    b = _add(conn, title="b", rank=2.0)
    writer.edit(conn, workspace_id="ws", kind="plan_step", object_id=a, fields={"rank": 3.0})
    steps = list_plan_steps(conn, "ws", "t1")
    assert [s.id for s in steps] == [b, a]


def test_valid_to_edit_drops_from_active_via_generic_writer(conn: sqlite3.Connection) -> None:
    """A valid_to edit through the generic writer drops a step from the
    active plan but keeps it in the trajectory — the behaviour the deleted
    remove_plan_step wrapper used to lock."""
    keep = _add(conn, title="keep")
    gone = _add(conn, title="gone")
    writer.edit(
        conn,
        workspace_id="ws",
        kind="plan_step",
        object_id=gone,
        fields={"valid_to": "2099-01-01T00:00:00Z"},
    )
    active = list_plan_steps(conn, "ws", "t1")
    assert [s.id for s in active] == [keep]
    all_steps = list_plan_steps(conn, "ws", "t1", include_removed=True)
    assert {s.id for s in all_steps} == {keep, gone}
