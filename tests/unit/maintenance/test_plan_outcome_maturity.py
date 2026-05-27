"""Unit tests for maintenance/plan_outcome_maturity.py (Phase 5c).

Uses the ``applied_conn`` fixture (full root-migration schema) -- it
carries plan_steps (with ``outcome_fed_at``), the capability maturity
tables, the capability_links table, and audit_log. Steps are created directly at
their target status via ``PlanStepIn``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.capability.usage_tracker import get_maturity_snapshot
from agent_memory_lite.config.settings import Settings
from agent_memory_lite.ingestion.capability_writer import upsert_agent_role, upsert_agent_skill
from agent_memory_lite.ingestion.plan_step_writer import add_plan_step
from agent_memory_lite.maintenance.brain_pass import (
    BrainPassReport,
    _step_feed_plan_outcomes,
    run_brain_pass,
)
from agent_memory_lite.maintenance.plan_outcome_maturity import feed_plan_step_outcomes
from agent_memory_lite.models.capabilities import AgentRoleIn, AgentSkillIn
from agent_memory_lite.models.enums import (
    CapabilityLinkRelation,
    CapabilityLinkTargetType,
    CapabilityType,
)
from agent_memory_lite.models.plan_step import PlanStepIn
from agent_memory_lite.repositories.capability_links_repo import upsert_capability_link_row

_WS = "ws"


def _make_skill(conn: sqlite3.Connection, name: str) -> str:
    """Create an agent_skill, return its id."""
    skill = upsert_agent_skill(
        conn, AgentSkillIn(workspace_id=_WS, name=name, summary=f"summary of {name}")
    )
    return skill.id


def _make_step(conn: sqlite3.Connection, task_id: str, title: str, status: str) -> str:
    """Create one plan step at the given status, return its id."""
    out = add_plan_step(
        conn,
        step_in=PlanStepIn(
            workspace_id=_WS,
            task_id=task_id,
            title=title,
            status=status,  # type: ignore[arg-type]
        ),
    )
    assert out is not None
    return str(out["id"])


def _link_cap(
    conn: sqlite3.Connection,
    step_id: str,
    capability_id: str,
    capability_type: CapabilityType = CapabilityType.SKILL,
) -> None:
    """Bind a capability (a skill by default) to a plan step."""
    upsert_capability_link_row(
        conn,
        link_id=f"cl_{step_id}_{capability_id}",
        workspace_id=_WS,
        target_type=CapabilityLinkTargetType.PLAN_STEP,
        target_id=step_id,
        capability_type=capability_type,
        capability_id=capability_id,
        capability_name="bound capability",
        relation=CapabilityLinkRelation.METHOD,
        rationale=None,
        strength=0.7,
        source_episode_id=None,
        created_at="2026-05-22T00:00:00Z",
        updated_at="2026-05-22T00:00:00Z",
    )


def _counts(conn: sqlite3.Connection, skill_id: str) -> tuple[int, int]:
    """(success_count, failure_count) for a skill."""
    snap = get_maturity_snapshot(conn, workspace_id=_WS, kind="skill", capability_id=skill_id)
    assert snap is not None
    return snap.success_count, snap.failure_count


def test_done_step_feeds_success(applied_conn: sqlite3.Connection) -> None:
    """A done step bumps its bound skill's success_count."""
    skill = _make_skill(applied_conn, "s1")
    step = _make_step(applied_conn, "t1", "build it", "done")
    _link_cap(applied_conn, step, skill)
    recorded = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50)
    assert recorded == 1
    assert _counts(applied_conn, skill) == (1, 0)


def test_blocked_step_not_fed_until_resolved(applied_conn: sqlite3.Connection) -> None:
    """A blocked step is transient, not terminal -- it is not fed. When the
    block resolves to done the step is fed as a success, never stuck as a
    stale failure (the Phase 5c round-3 audit finding)."""
    skill = _make_skill(applied_conn, "s1")
    step = _make_step(applied_conn, "t1", "stuck then done", "blocked")
    _link_cap(applied_conn, step, skill)
    assert feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50) == 0
    assert _counts(applied_conn, skill) == (0, 0)
    applied_conn.execute("UPDATE plan_steps SET status = 'done' WHERE id = ?", (step,))
    applied_conn.commit()
    assert feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50) == 1
    assert _counts(applied_conn, skill) == (1, 0)


def test_skipped_step_feeds_failure(applied_conn: sqlite3.Connection) -> None:
    """A skipped step counts as a failure for its bound skill."""
    skill = _make_skill(applied_conn, "s1")
    step = _make_step(applied_conn, "t1", "detour", "skipped")
    _link_cap(applied_conn, step, skill)
    feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50)
    assert _counts(applied_conn, skill) == (0, 1)


def test_pending_and_active_steps_not_fed(applied_conn: sqlite3.Connection) -> None:
    """Non-terminal steps (pending / active) carry no outcome yet."""
    skill = _make_skill(applied_conn, "s1")
    for status in ("pending", "active"):
        step = _make_step(applied_conn, "t1", status, status)
        _link_cap(applied_conn, step, skill)
    assert feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50) == 0
    assert _counts(applied_conn, skill) == (0, 0)


def test_outcome_fed_exactly_once(applied_conn: sqlite3.Connection) -> None:
    """A re-running pass never re-feeds a step — outcome_fed_at gates it."""
    skill = _make_skill(applied_conn, "s1")
    step = _make_step(applied_conn, "t1", "build it", "done")
    _link_cap(applied_conn, step, skill)
    first = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50)
    second = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50)
    assert (first, second) == (1, 0)
    assert _counts(applied_conn, skill) == (1, 0)  # bumped once, not twice


def test_step_without_capabilities_is_marked_fed(applied_conn: sqlite3.Connection) -> None:
    """A terminal step with no bound capability records nothing but is
    still stamped, so it is not re-scanned every pass."""
    _make_step(applied_conn, "t1", "unbound", "done")
    first = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50)
    row = applied_conn.execute(
        "SELECT outcome_fed_at FROM plan_steps WHERE task_id = 't1'"
    ).fetchone()
    assert first == 0
    assert row[0] is not None  # stamped despite zero links


def test_removed_step_not_fed(applied_conn: sqlite3.Connection) -> None:
    """A re-planned-out step (valid_to set) is excluded."""
    skill = _make_skill(applied_conn, "s1")
    step = _make_step(applied_conn, "t1", "dropped", "done")
    _link_cap(applied_conn, step, skill)
    applied_conn.execute(
        "UPDATE plan_steps SET valid_to = '2099-01-01T00:00:00Z' WHERE id = ?", (step,)
    )
    applied_conn.commit()
    assert feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50) == 0
    assert _counts(applied_conn, skill) == (0, 0)


def test_max_steps_caps_the_pass(applied_conn: sqlite3.Connection) -> None:
    """max_steps bounds steps fed per pass; the rest clear next pass."""
    skill = _make_skill(applied_conn, "s1")
    for i in range(3):
        step = _make_step(applied_conn, "t1", f"step {i}", "done")
        _link_cap(applied_conn, step, skill)
    first = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=2)
    second = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=2)
    assert (first, second) == (2, 1)
    assert _counts(applied_conn, skill) == (3, 0)


def test_feed_on_db_without_plan_steps_is_noop() -> None:
    """A DB with no plan_steps table (legacy) returns 0, never raises."""
    conn = sqlite3.connect(":memory:")
    try:
        assert feed_plan_step_outcomes(conn, workspace_id=_WS, max_steps=50) == 0
    finally:
        conn.close()


def test_step_disabled_is_inert(applied_conn: sqlite3.Connection) -> None:
    """plan_outcome_maturity_enabled=False → the brain-pass step is a no-op."""
    skill = _make_skill(applied_conn, "s1")
    step = _make_step(applied_conn, "t1", "build it", "done")
    _link_cap(applied_conn, step, skill)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_PLAN_OUTCOME_MATURITY_ENABLED="false",
    )
    report = BrainPassReport(workspace_id=_WS, started_at="t", finished_at="t")
    _step_feed_plan_outcomes(applied_conn, _WS, settings, report)
    assert report.plan_step_outcomes_fed == 0
    assert _counts(applied_conn, skill) == (0, 0)


def test_step_enabled_records_count(applied_conn: sqlite3.Connection) -> None:
    """Enabled, the brain-pass step feeds outcomes, records the count, and
    stays failure-soft (no errors)."""
    skill = _make_skill(applied_conn, "s1")
    step = _make_step(applied_conn, "t1", "build it", "done")
    _link_cap(applied_conn, step, skill)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OLLAMA_PROBE_SKIP="true",
        MEMORY_PLAN_OUTCOME_MATURITY_ENABLED="true",
    )
    report = BrainPassReport(workspace_id=_WS, started_at="t", finished_at="t")
    _step_feed_plan_outcomes(applied_conn, _WS, settings, report)
    assert report.plan_step_outcomes_fed == 1
    assert report.errors == []


def test_run_brain_pass_feeds_and_reports(applied_conn: sqlite3.Connection) -> None:
    """A full brain pass feeds terminal-step outcomes and surfaces the
    count in BrainPassReport.to_dict() (telemetry for /health + dashboards)."""
    skill = _make_skill(applied_conn, "s1")
    step = _make_step(applied_conn, "t1", "build it", "done")
    _link_cap(applied_conn, step, skill)
    settings = Settings(_env_file=None, OLLAMA_PROBE_SKIP="true")  # type: ignore[call-arg]
    report = run_brain_pass(applied_conn, workspace_id=_WS, settings=settings)
    assert report.plan_step_outcomes_fed == 1
    assert report.to_dict()["plan_step_outcomes_fed"] == 1
    assert _counts(applied_conn, skill) == (1, 0)


def _make_role(conn: sqlite3.Connection, name: str) -> str:
    """Create an agent_role, return its id."""
    role = upsert_agent_role(
        conn, AgentRoleIn(workspace_id=_WS, name=name, purpose=f"purpose of {name}")
    )
    return role.id


def test_role_capability_outcome_fed(applied_conn: sqlite3.Connection) -> None:
    """A done step feeds success into a bound ROLE's counters too — the
    capability_type passes straight through to usage_tracker, not just
    the skill path."""
    role = _make_role(applied_conn, "r1")
    step = _make_step(applied_conn, "t1", "lead it", "done")
    _link_cap(applied_conn, step, role, CapabilityType.ROLE)
    recorded = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50)
    assert recorded == 1
    snap = get_maturity_snapshot(applied_conn, workspace_id=_WS, kind="role", capability_id=role)
    assert snap is not None
    assert (snap.success_count, snap.failure_count) == (1, 0)


def test_dangling_link_recorded_as_zero(applied_conn: sqlite3.Connection) -> None:
    """A link to a capability that does not exist records nothing, but the
    step is still marked fed — a second pass re-scans nothing."""
    step = _make_step(applied_conn, "t1", "orphan link", "done")
    _link_cap(applied_conn, step, "skill_does_not_exist")
    first = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50)
    second = feed_plan_step_outcomes(applied_conn, workspace_id=_WS, max_steps=50)
    assert (first, second) == (0, 0)
