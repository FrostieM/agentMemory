"""Stale-capability hygiene finder runs over agent_skills/roles/playbooks."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.maintenance.hygiene_capability_maturity import find_stale_capabilities


def _seed_skill(
    conn: sqlite3.Connection,
    *,
    skill_id: str,
    last_invoked_at: str | None,
    active: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_skills
        (id, workspace_id, name, summary, when_to_use_json, inputs_json,
         outputs_json, tools_json, related_roles_json, source_episode_id,
         confidence, active, created_at, updated_at,
         usage_count, success_count, failure_count, last_invoked_at)
        VALUES (?, 'default', ?, 'summary', '[]', '[]', '[]', '[]', '[]',
                NULL, 0.7, ?, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z',
                0, 0, 0, ?)
        """,
        (skill_id, f"name-{skill_id}", active, last_invoked_at),
    )


def test_no_capabilities_yields_no_findings(applied_conn: sqlite3.Connection) -> None:
    findings = find_stale_capabilities(applied_conn, workspace_id="default", stale_days=60)
    assert findings == []


def test_capability_with_null_last_invoked_skipped(applied_conn: sqlite3.Connection) -> None:
    _seed_skill(applied_conn, skill_id="sk_a", last_invoked_at=None)
    findings = find_stale_capabilities(applied_conn, workspace_id="default", stale_days=60)
    assert findings == []


def test_capability_invoked_today_not_stale(applied_conn: sqlite3.Connection) -> None:
    _seed_skill(
        applied_conn,
        skill_id="sk_a",
        last_invoked_at=datetime.now(UTC).isoformat(),
    )
    findings = find_stale_capabilities(applied_conn, workspace_id="default", stale_days=60)
    assert findings == []


def test_capability_idle_past_window_is_flagged(applied_conn: sqlite3.Connection) -> None:
    long_ago = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    _seed_skill(applied_conn, skill_id="sk_old", last_invoked_at=long_ago)
    findings = find_stale_capabilities(applied_conn, workspace_id="default", stale_days=60)
    assert len(findings) == 1
    assert findings[0].kind == "stale_capability"
    assert findings[0].severity == "info"
    assert findings[0].target_id == "sk_old"


def test_inactive_capability_not_flagged(applied_conn: sqlite3.Connection) -> None:
    long_ago = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    _seed_skill(
        applied_conn,
        skill_id="sk_inactive",
        last_invoked_at=long_ago,
        active=0,
    )
    findings = find_stale_capabilities(applied_conn, workspace_id="default", stale_days=60)
    assert findings == []
