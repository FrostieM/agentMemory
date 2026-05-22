"""Unit tests for maintenance/plan_playbook_distiller.py (Phase 5b).

Uses the ``applied_conn`` fixture (full root-migration schema) — it
carries plan_steps, agent_playbooks and audit_log. Steps are created
directly at their target status via ``PlanStepIn``, so no ``writer.edit``
(and no ``versions`` table) is needed.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.plan_step_writer import add_plan_step
from agent_memory_lite.maintenance.brain_pass import (
    BrainPassReport,
    _step_distill_plan_playbooks,
    run_brain_pass,
)
from agent_memory_lite.maintenance.plan_playbook_distiller import (
    _is_complete,
    distill_completed_plans,
)
from agent_memory_lite.models.plan_step import PlanStepIn
from agent_memory_lite.repositories.capabilities_repo import get_playbook_by_name


def _make_plan(conn: sqlite3.Connection, task_id: str, *statuses: str) -> None:
    """Create a plan whose steps carry the given statuses, titled
    ``<task_id>-step-<i>`` in rank order."""
    for i, status in enumerate(statuses):
        out = add_plan_step(
            conn,
            step_in=PlanStepIn(
                workspace_id="ws",
                task_id=task_id,
                title=f"{task_id}-step-{i}",
                status=status,  # type: ignore[arg-type]
            ),
        )
        assert out is not None


def test_complete_plan_distills_to_playbook(applied_conn: sqlite3.Connection) -> None:
    """All steps done → a plan:<task_id> playbook with the step titles."""
    _make_plan(applied_conn, "t1", "done", "done", "done")
    assert distill_completed_plans(applied_conn, workspace_id="ws", max_distill=10) == 1
    pb = get_playbook_by_name(applied_conn, workspace_id="ws", name="plan:t1")
    assert pb is not None
    assert pb.steps == ["t1-step-0", "t1-step-1", "t1-step-2"]
    assert pb.active is True


def test_incomplete_plan_not_distilled(applied_conn: sqlite3.Connection) -> None:
    """A plan with a still-active step is not finished → no playbook."""
    _make_plan(applied_conn, "t1", "done", "active")
    assert distill_completed_plans(applied_conn, workspace_id="ws", max_distill=10) == 0
    assert get_playbook_by_name(applied_conn, workspace_id="ws", name="plan:t1") is None


def test_all_skipped_plan_not_distilled(applied_conn: sqlite3.Connection) -> None:
    """A plan with no done steps was abandoned, not executed → no playbook."""
    _make_plan(applied_conn, "t1", "skipped", "skipped")
    assert distill_completed_plans(applied_conn, workspace_id="ws", max_distill=10) == 0
    assert get_playbook_by_name(applied_conn, workspace_id="ws", name="plan:t1") is None


def test_done_and_skipped_mix_distills_done_titles_only(applied_conn: sqlite3.Connection) -> None:
    """done + skipped is complete; skipped steps drop out of the recipe."""
    _make_plan(applied_conn, "t1", "done", "skipped", "done")
    distill_completed_plans(applied_conn, workspace_id="ws", max_distill=10)
    pb = get_playbook_by_name(applied_conn, workspace_id="ws", name="plan:t1")
    assert pb is not None
    assert pb.steps == ["t1-step-0", "t1-step-2"]


def test_distill_is_idempotent(applied_conn: sqlite3.Connection) -> None:
    """A second pass over an already-distilled plan is a no-op."""
    _make_plan(applied_conn, "t1", "done")
    first = distill_completed_plans(applied_conn, workspace_id="ws", max_distill=10)
    second = distill_completed_plans(applied_conn, workspace_id="ws", max_distill=10)
    assert (first, second) == (1, 0)


def test_max_distill_caps_new_playbooks(applied_conn: sqlite3.Connection) -> None:
    """max_distill bounds NEW playbooks per pass; the rest clear next pass."""
    for task_id in ("t1", "t2", "t3"):
        _make_plan(applied_conn, task_id, "done")
    first = distill_completed_plans(applied_conn, workspace_id="ws", max_distill=2)
    second = distill_completed_plans(applied_conn, workspace_id="ws", max_distill=2)
    assert (first, second) == (2, 1)


def test_is_complete_rejects_empty_plan() -> None:
    """A plan with no steps is never complete."""
    assert _is_complete([]) is False


def test_non_terminal_step_blocks_distillation(applied_conn: sqlite3.Connection) -> None:
    """A blocked or pending step (neither done nor skipped) keeps the plan
    incomplete — distillation must not fire."""
    _make_plan(applied_conn, "t1", "done", "blocked")
    _make_plan(applied_conn, "t2", "done", "pending")
    assert distill_completed_plans(applied_conn, workspace_id="ws", max_distill=10) == 0
    assert get_playbook_by_name(applied_conn, workspace_id="ws", name="plan:t1") is None
    assert get_playbook_by_name(applied_conn, workspace_id="ws", name="plan:t2") is None


def test_distill_on_db_without_plan_steps_is_noop() -> None:
    """A DB with no plan_steps table (legacy / empty) returns 0, never raises."""
    conn = sqlite3.connect(":memory:")
    try:
        assert distill_completed_plans(conn, workspace_id="ws", max_distill=10) == 0
    finally:
        conn.close()


def test_removed_step_excluded_from_playbook(applied_conn: sqlite3.Connection) -> None:
    """A re-planned-out step (valid_to set) is not part of the distilled recipe."""
    _make_plan(applied_conn, "t1", "done", "done")
    rows = applied_conn.execute(
        "SELECT id FROM plan_steps WHERE task_id = 't1' ORDER BY rank"
    ).fetchall()
    applied_conn.execute(
        "UPDATE plan_steps SET valid_to = '2099-01-01T00:00:00Z' WHERE id = ?",
        (rows[1][0],),
    )
    applied_conn.commit()
    assert distill_completed_plans(applied_conn, workspace_id="ws", max_distill=10) == 1
    pb = get_playbook_by_name(applied_conn, workspace_id="ws", name="plan:t1")
    assert pb is not None
    assert pb.steps == ["t1-step-0"]  # the re-planned-out step-1 is excluded


def test_step_distill_disabled_is_inert(applied_conn: sqlite3.Connection) -> None:
    """plan_playbook_distill_enabled=False → the brain-pass step is a no-op."""
    _make_plan(applied_conn, "t1", "done")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_PLAN_PLAYBOOK_DISTILL_ENABLED="false",
    )
    report = BrainPassReport(workspace_id="ws", started_at="t", finished_at="t")
    _step_distill_plan_playbooks(applied_conn, "ws", settings, report)
    assert report.plan_playbooks_distilled == 0
    assert get_playbook_by_name(applied_conn, workspace_id="ws", name="plan:t1") is None


def test_step_distill_enabled_records_count(applied_conn: sqlite3.Connection) -> None:
    """Enabled, the brain-pass step distils completed plans, records the count
    on the report, and stays failure-soft (no errors)."""
    _make_plan(applied_conn, "t1", "done")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_PLAN_PLAYBOOK_DISTILL_ENABLED="true",
    )
    report = BrainPassReport(workspace_id="ws", started_at="t", finished_at="t")
    _step_distill_plan_playbooks(applied_conn, "ws", settings, report)
    assert report.plan_playbooks_distilled == 1
    assert report.errors == []


def test_run_brain_pass_distills_and_reports(applied_conn: sqlite3.Connection) -> None:
    """The step is genuinely wired into run_brain_pass: a full pass over a
    workspace with a completed plan distils it, and the count surfaces in
    BrainPassReport.to_dict() (telemetry for /health + dashboards)."""
    _make_plan(applied_conn, "t1", "done")
    settings = Settings(_env_file=None, OLLAMA_PROBE_SKIP="true")  # type: ignore[call-arg]
    report = run_brain_pass(applied_conn, workspace_id="ws", settings=settings)
    assert report.plan_playbooks_distilled == 1
    assert report.to_dict()["plan_playbooks_distilled"] == 1
