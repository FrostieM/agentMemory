"""v3.6 critical fix: brief surfaces pinned behaviors by priority.

Pinned behavior rows now live in the canonical ``behaviors`` table.
These tests lock the ordering rules without keeping v2 fixture tables alive.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from agent_memory_lite.cognition.brief import _build_pinned_behaviors


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(":memory:")  # type: ignore[arg-type]
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_behavior(
    conn: sqlite3.Connection,
    *,
    id_: str,
    name: str,
    rule: str,
    priority: str,
    pinned: bool = True,
    active: bool = True,
    updated_at: str = "2026-05-20T00:00:00+00:00",
) -> None:
    conn.execute(
        """INSERT INTO behaviors
           (id, workspace_id, name, kind, scope, priority, rule, rule_one_line,
            rationale, applies_to_json, conflict_policy, source_type, source_id,
            confidence, importance, pinned, active, created_at, updated_at,
            application_count)
           VALUES (?, 'ws', ?, 'operating_rule', 'workspace', ?, ?, ?, '', '[]',
                   'current_user_wins', 'manual', NULL, 0.9, 0.8, ?, ?, ?, ?, 0)""",
        (
            id_,
            name,
            priority,
            rule,
            rule,
            1 if pinned else 0,
            1 if active else 0,
            updated_at,
            updated_at,
        ),
    )
    conn.commit()


def test_user_preference_outranks_project_convention(conn: sqlite3.Connection) -> None:
    _seed_behavior(
        conn,
        id_="beh_recent_proj",
        name="recent-project-rule",
        rule="conventional formatting",
        priority="project_convention",
        updated_at="2026-05-20T23:00:00+00:00",
    )
    _seed_behavior(
        conn,
        id_="beh_user_pref",
        name="critical-user-rule",
        rule="ask before destructive action",
        priority="user_preference",
        updated_at="2026-05-19T00:00:00+00:00",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    text = "\n".join(section.lines)
    user_idx = text.find("critical-user-rule")
    proj_idx = text.find("recent-project-rule")
    assert user_idx >= 0
    assert proj_idx >= 0
    assert user_idx < proj_idx


def test_system_bound_outranks_user_preference(conn: sqlite3.Connection) -> None:
    _seed_behavior(
        conn,
        id_="beh_sys",
        name="system-rule",
        rule="hard invariant",
        priority="system_bound",
        updated_at="2026-05-18T00:00:00+00:00",
    )
    _seed_behavior(
        conn,
        id_="beh_user",
        name="user-rule",
        rule="user pref",
        priority="user_preference",
        updated_at="2026-05-20T00:00:00+00:00",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    text = "\n".join(section.lines)
    assert text.find("system-rule") < text.find("user-rule")


def test_operator_written_behavior_surfaces(conn: sqlite3.Connection) -> None:
    _seed_behavior(
        conn,
        id_="beh_operator",
        name="operator-rule",
        rule="from HTTP upsert",
        priority="user_preference",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    assert "operator-rule" in "\n".join(section.lines)


def test_archived_pinned_does_not_surface(conn: sqlite3.Connection) -> None:
    _seed_behavior(
        conn,
        id_="beh_inactive",
        name="archived-rule",
        rule="old",
        priority="user_preference",
        active=False,
    )
    _seed_behavior(
        conn,
        id_="beh_active",
        name="live-rule",
        rule="current",
        priority="project_convention",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    text = "\n".join(section.lines)
    assert "archived-rule" not in text
    assert "live-rule" in text


def test_no_pinned_returns_only_header(conn: sqlite3.Connection) -> None:
    section = _build_pinned_behaviors(conn, "ws", budget=200)
    assert section.lines == ["## Pinned behaviors"]


def test_unknown_priority_treated_as_project_convention(
    conn: sqlite3.Connection,
) -> None:
    _seed_behavior(
        conn,
        id_="beh_weird",
        name="weird-prio",
        rule="x",
        priority="some_future_priority",
    )
    _seed_behavior(
        conn,
        id_="beh_user",
        name="user-rule",
        rule="y",
        priority="user_preference",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    text = "\n".join(section.lines)
    assert text.find("user-rule") < text.find("weird-prio")
