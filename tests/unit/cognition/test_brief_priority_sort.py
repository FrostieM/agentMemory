"""v3.6 critical fix: brief surfaces pinned behaviors by PRIORITY.

The pre-fix code sorted pinned behaviors by ``updated_at DESC`` and
capped at top-12. With 20+ pinned rules in the workspace, recently-
updated ``project_convention`` rules pushed safety-critical
``user_preference`` rules (including the no-push rule) out of the
brief — the dominant cause of the 2026-05-20 push-without-approval
incident where the agent ran 7 unauthorized pushes to main.

Also: behaviors live in TWO tables on this DB:
- ``behaviors`` — legacy + insight-promotion path
- ``behavior_instructions`` — canonical HTTP write surface

The brief used to read only the first table, so any operator-written
behavior via /memory/upsert_behavior_instruction was invisible.
This regression locks BOTH fixes together.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_memory_lite.cognition.brief import _build_pinned_behaviors

CANONICAL_INIT = Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0001_init.sql"
CANONICAL_OUTCOME = (
    Path(__file__).resolve().parents[3] / "migrations" / "canonical" / "0002_outcome_loop.sql"
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """Hybrid schema: canonical (provides ``behaviors``) + main
    migrations (provides ``behavior_instructions``). The brief reads
    both tables; the test must seed both."""
    from agent_memory_lite.db.connection import open_connection  # noqa: PLC0415
    from agent_memory_lite.db.migrations import apply_migrations  # noqa: PLC0415

    c = open_connection(":memory:")  # type: ignore[arg-type]
    apply_migrations(c)
    c.executescript(CANONICAL_INIT.read_text(encoding="utf-8"))
    c.executescript(CANONICAL_OUTCOME.read_text(encoding="utf-8"))
    try:
        yield c
    finally:
        c.close()


def _seed_behavior(
    conn: sqlite3.Connection,
    table: str,
    *,
    id_: str,
    name: str,
    rule: str,
    priority: str,
    pinned: bool = True,
    active: bool = True,
    updated_at: str = "2026-05-20T00:00:00+00:00",
) -> None:
    """Seed a row into either ``behaviors`` or ``behavior_instructions``.
    The two tables have slightly different schemas; this helper papers
    over the difference."""
    if table == "behaviors":
        # behaviors uses rule + rule_one_line
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
    else:  # behavior_instructions
        conn.execute(
            """INSERT INTO behavior_instructions
               (id, workspace_id, name, kind, scope, priority, rule, rationale,
                applies_to_json, conflict_policy, source_episode_id, confidence,
                active, created_at, updated_at, source_type, application_count, pinned)
               VALUES (?, 'ws', ?, 'operating_rule', 'workspace', ?, ?, '', '[]',
                       'current_user_wins', NULL, 0.9, ?, ?, ?, 'manual', 0, ?)""",
            (
                id_,
                name,
                priority,
                rule,
                1 if active else 0,
                updated_at,
                updated_at,
                1 if pinned else 0,
            ),
        )
    conn.commit()


def test_user_preference_outranks_project_convention(conn: sqlite3.Connection) -> None:
    """The dominant pre-fix bug: a recently-updated project_convention
    rule pushed user_preference rules out of the top-N. The fix sorts
    by priority weight FIRST, then updated_at as tiebreaker."""
    # Seed a recent project_convention rule (highest updated_at)
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_recent_proj",
        name="recent-project-rule",
        rule="conventional formatting",
        priority="project_convention",
        updated_at="2026-05-20T23:00:00+00:00",
    )
    # Seed an older user_preference rule (LOWER updated_at)
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_user_pref",
        name="critical-user-rule",
        rule="ask before destructive action",
        priority="user_preference",
        updated_at="2026-05-19T00:00:00+00:00",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    # user_preference must come BEFORE project_convention despite
    # the timestamp ordering.
    text = "\n".join(section.lines)
    user_idx = text.find("critical-user-rule")
    proj_idx = text.find("recent-project-rule")
    assert user_idx >= 0, "user_preference rule must surface"
    assert proj_idx >= 0, "project_convention rule must surface"
    assert user_idx < proj_idx, "user_preference must rank above project_convention in the brief"


def test_system_bound_outranks_user_preference(conn: sqlite3.Connection) -> None:
    """Full priority order: system_bound > user_preference > ..."""
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_sys",
        name="system-rule",
        rule="hard invariant",
        priority="system_bound",
        updated_at="2026-05-18T00:00:00+00:00",
    )
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_user",
        name="user-rule",
        rule="user pref",
        priority="user_preference",
        updated_at="2026-05-20T00:00:00+00:00",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    text = "\n".join(section.lines)
    assert text.find("system-rule") < text.find("user-rule")


def test_reads_both_behavior_tables(conn: sqlite3.Connection) -> None:
    """v3.6 fix: pinned behaviors written via /memory/upsert_behavior_
    instruction land in ``behavior_instructions``, not ``behaviors``.
    Pre-fix, the brief only read ``behaviors`` and silently dropped
    every operator-written rule."""
    # behaviors row (legacy / insight-promotion path)
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_legacy",
        name="legacy-rule",
        rule="from compaction",
        priority="project_convention",
    )
    # behavior_instructions row (canonical operator path)
    _seed_behavior(
        conn,
        "behavior_instructions",
        id_="beh_canonical",
        name="canonical-rule",
        rule="from HTTP upsert",
        priority="user_preference",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    text = "\n".join(section.lines)
    # BOTH must surface
    assert "legacy-rule" in text
    assert "canonical-rule" in text
    # And the user_preference one ranks first
    assert text.find("canonical-rule") < text.find("legacy-rule")


def test_dedup_by_name_across_tables(conn: sqlite3.Connection) -> None:
    """If the same name exists in BOTH tables (operator wrote v2 then
    v3), the brief shows it once. Otherwise we'd waste budget on
    duplicates."""
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_dup_v1",
        name="same-name",
        rule="version 1",
        priority="project_convention",
    )
    _seed_behavior(
        conn,
        "behavior_instructions",
        id_="beh_dup_v2",
        name="same-name",
        rule="version 2",
        priority="user_preference",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    text = "\n".join(section.lines)
    # Exactly one mention of "same-name" — the higher-priority entry
    # (user_preference from behavior_instructions) wins via sort.
    assert text.count("same-name") == 1


def test_archived_pinned_does_not_surface(conn: sqlite3.Connection) -> None:
    """A pinned rule with ``active=0`` is the operator's "deprecated
    but keep for audit" state — must NOT surface."""
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_inactive",
        name="archived-rule",
        rule="old",
        priority="user_preference",
        active=False,
    )
    _seed_behavior(
        conn,
        "behaviors",
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
    """Empty pool → just the section header (no rows shown)."""
    section = _build_pinned_behaviors(conn, "ws", budget=200)
    assert section.lines == ["## Pinned behaviors"]


def test_unknown_priority_treated_as_project_convention(
    conn: sqlite3.Connection,
) -> None:
    """A behavior with an unknown priority string falls back to
    project_convention weight (1.0 default). Lock the safe fallback."""
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_weird",
        name="weird-prio",
        rule="x",
        priority="some_future_priority",
    )
    _seed_behavior(
        conn,
        "behaviors",
        id_="beh_user",
        name="user-rule",
        rule="y",
        priority="user_preference",
    )
    section = _build_pinned_behaviors(conn, "ws", budget=1500)
    text = "\n".join(section.lines)
    # user_preference > the unknown one (which falls back to project_convention)
    assert text.find("user-rule") < text.find("weird-prio")
