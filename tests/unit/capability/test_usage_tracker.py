"""Verify the capability invocation/outcome chokepoint actually moves the
counters AND writes audit rows. These tests run against a tmp DB with the
real migrations applied so no schema drift can hide.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory_lite.capability.usage_tracker import (
    SUPPORTED_KINDS,
    get_maturity_snapshot,
    record_invocation,
    record_outcome,
)


def _seed_skill(conn: sqlite3.Connection, *, skill_id: str = "sk_alpha") -> None:
    conn.execute(
        """
        INSERT INTO agent_skills
        (id, workspace_id, name, summary, when_to_use_json, inputs_json,
         outputs_json, tools_json, related_roles_json, source_episode_id,
         confidence, active, created_at, updated_at)
        VALUES (?, 'default', 'Test skill', 'summary', '[]', '[]', '[]',
                '[]', '[]', NULL, 0.7, 1, '2026-01-01T00:00:00Z',
                '2026-01-01T00:00:00Z')
        """,
        (skill_id,),
    )


def _audit_actions(conn: sqlite3.Connection, target_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT action FROM audit_log WHERE target_id = ? ORDER BY created_at",
        (target_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def test_record_invocation_bumps_usage_count_and_audits(
    applied_conn: sqlite3.Connection,
) -> None:
    _seed_skill(applied_conn)
    assert record_invocation(
        applied_conn, workspace_id="default", kind="skill", capability_id="sk_alpha"
    )
    snap = get_maturity_snapshot(
        applied_conn, workspace_id="default", kind="skill", capability_id="sk_alpha"
    )
    assert snap is not None
    assert snap.usage_count == 1
    assert snap.last_invoked_at is not None
    assert "capability.invocation_recorded" in _audit_actions(applied_conn, "sk_alpha")


def test_record_invocation_returns_false_for_unknown_id(
    applied_conn: sqlite3.Connection,
) -> None:
    assert (
        record_invocation(
            applied_conn, workspace_id="default", kind="skill", capability_id="missing"
        )
        is False
    )
    # No audit row written when nothing was updated.
    assert _audit_actions(applied_conn, "missing") == []


def test_record_outcome_success_increments_success_count(
    applied_conn: sqlite3.Connection,
) -> None:
    _seed_skill(applied_conn)
    assert record_outcome(
        applied_conn,
        workspace_id="default",
        kind="skill",
        capability_id="sk_alpha",
        success=True,
    )
    snap = get_maturity_snapshot(
        applied_conn, workspace_id="default", kind="skill", capability_id="sk_alpha"
    )
    assert snap is not None
    assert snap.success_count == 1
    assert snap.failure_count == 0


def test_record_outcome_failure_increments_failure_count(
    applied_conn: sqlite3.Connection,
) -> None:
    _seed_skill(applied_conn)
    assert record_outcome(
        applied_conn,
        workspace_id="default",
        kind="skill",
        capability_id="sk_alpha",
        success=False,
    )
    snap = get_maturity_snapshot(
        applied_conn, workspace_id="default", kind="skill", capability_id="sk_alpha"
    )
    assert snap is not None
    assert snap.failure_count == 1
    assert snap.success_count == 0
    actions = _audit_actions(applied_conn, "sk_alpha")
    assert "capability.outcome_recorded" in actions


def test_unsupported_kind_raises() -> None:
    # Don't need a connection — guard runs first.
    with pytest.raises(ValueError, match="unsupported capability kind"):
        record_invocation(
            None,  # type: ignore[arg-type]
            workspace_id="default",
            kind="bogus",
            capability_id="x",
        )


def test_supported_kinds_constant_is_complete() -> None:
    assert set(SUPPORTED_KINDS) == {"skill", "role", "playbook"}


def test_invocation_then_outcome_order_is_natural(
    applied_conn: sqlite3.Connection,
) -> None:
    _seed_skill(applied_conn)
    record_invocation(applied_conn, workspace_id="default", kind="skill", capability_id="sk_alpha")
    record_outcome(
        applied_conn,
        workspace_id="default",
        kind="skill",
        capability_id="sk_alpha",
        success=True,
    )
    snap = get_maturity_snapshot(
        applied_conn, workspace_id="default", kind="skill", capability_id="sk_alpha"
    )
    assert snap is not None
    assert snap.usage_count == 1
    assert snap.success_count == 1
    actions = _audit_actions(applied_conn, "sk_alpha")
    assert actions == ["capability.invocation_recorded", "capability.outcome_recorded"]
