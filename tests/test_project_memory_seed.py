from __future__ import annotations

import json
import sqlite3

from agent_memory_lite.bootstrap.project_memory_seed import (
    PROFILE_NAME,
    seed_neutral_project_memory,
)


def _count(conn: sqlite3.Connection, table: str, workspace_id: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    assert row is not None
    return int(row["n"])


def test_neutral_project_memory_seed_writes_only_population_helpers(
    applied_conn: sqlite3.Connection,
) -> None:
    result = seed_neutral_project_memory(applied_conn, workspace_id="project-x")

    assert result.profile == PROFILE_NAME
    assert result.roles_written == 0
    # 1.2.3: seed now writes ONE generic discipline behavior_instruction
    # (Link capability after every decision/theory). No project-specific
    # personality, language, or style — only the discipline rule.
    assert result.behavior_instructions_written == 1
    assert [item.name for item in result.skills] == ["Memory population discipline"]
    assert [item.name for item in result.playbooks] == ["Neutral memory bootstrap"]
    assert {item.name for item in result.concepts} == {
        "workspace_id",
        "memory candidate review",
        "memory snapshot",
        "memory integrity audit",
    }
    assert [item.name for item in result.behavior_instructions] == [
        "Link capability after every decision and theory write",
    ]

    assert _count(applied_conn, "agent_roles", "project-x") == 0
    assert _count(applied_conn, "behavior_instructions", "project-x") == 1
    assert _count(applied_conn, "agent_skills", "project-x") == 1
    assert _count(applied_conn, "agent_playbooks", "project-x") == 1
    assert _count(applied_conn, "domain_concepts", "project-x") == 4


def test_neutral_project_memory_seed_is_idempotent(applied_conn: sqlite3.Connection) -> None:
    first = seed_neutral_project_memory(applied_conn, workspace_id="project-x")
    second = seed_neutral_project_memory(applied_conn, workspace_id="project-x")

    assert first.skills[0].id == second.skills[0].id
    assert first.playbooks[0].id == second.playbooks[0].id
    assert {item.id for item in first.concepts} == {item.id for item in second.concepts}
    # Behavior_instruction upsert is also idempotent on (workspace_id, name)
    assert first.behavior_instructions[0].id == second.behavior_instructions[0].id
    assert _count(applied_conn, "agent_skills", "project-x") == 1
    assert _count(applied_conn, "agent_playbooks", "project-x") == 1
    assert _count(applied_conn, "domain_concepts", "project-x") == 4
    assert _count(applied_conn, "behavior_instructions", "project-x") == 1


def test_seed_behavior_instruction_metadata(applied_conn: sqlite3.Connection) -> None:
    """1.2.3: the seeded discipline rule must carry the right enum values
    and metadata so it's immediately visible in <behavior_instructions>
    of the next memory_get_context envelope."""
    seed_neutral_project_memory(applied_conn, workspace_id="project-x")
    row = applied_conn.execute(
        "SELECT name, kind, scope, priority, conflict_policy, source_type, "
        "active, applies_to_json FROM behavior_instructions "
        "WHERE workspace_id='project-x'"
    ).fetchone()
    assert row is not None
    assert row["name"] == "Link capability after every decision and theory write"
    assert row["kind"] == "operating_rule"
    assert row["scope"] == "workspace"
    assert row["priority"] == "user_preference"
    assert row["conflict_policy"] == "current_user_wins"
    assert row["source_type"] == "seed_bootstrap"
    assert row["active"] in (1, True)
    # applies_to should reference the four mutating research APIs
    applies_to = json.loads(row["applies_to_json"] or "[]")
    assert "memory_write_decision" in applies_to
    assert "memory_write_theory" in applies_to
