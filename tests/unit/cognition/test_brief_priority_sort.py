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


def test_rendered_behavior_is_credited_applied(conn: sqlite3.Connection) -> None:
    """Building the section credits each rendered behavior's application_count
    (unfreezes behaviors_fired_ratio) WITHOUT bumping updated_at -- so it cannot
    bust the brief cache (whose fingerprint hashes MAX(behaviors.updated_at))."""
    _seed_behavior(conn, id_="beh_x", name="rule-x", rule="do the thing", priority="system_bound")
    _build_pinned_behaviors(conn, "ws", budget=400)
    row = conn.execute(
        "SELECT application_count, last_applied_at, updated_at FROM behaviors WHERE id = 'beh_x'"
    ).fetchone()
    assert row["application_count"] == 1
    assert row["last_applied_at"] is not None
    assert row["updated_at"] == "2026-05-20T00:00:00+00:00"  # unchanged -> cache-safe


def test_only_budget_surviving_behaviors_are_credited(conn: sqlite3.Connection) -> None:
    """A behavior trimmed by the token budget is NOT credited -- only the bullets
    that actually reached the agent's envelope count as applied."""
    long_rule = "r" * 100
    _seed_behavior(conn, id_="beh_hi", name="hi", rule=long_rule, priority="system_bound")
    _seed_behavior(conn, id_="beh_lo", name="lo", rule=long_rule, priority="user_preference")
    _build_pinned_behaviors(conn, "ws", budget=8)  # header + ~1 bullet only
    hi = conn.execute("SELECT application_count FROM behaviors WHERE id = 'beh_hi'").fetchone()[0]
    lo = conn.execute("SELECT application_count FROM behaviors WHERE id = 'beh_lo'").fetchone()[0]
    assert hi == 1  # higher-priority, rendered -> credited
    assert lo == 0  # trimmed by budget -> not credited


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
