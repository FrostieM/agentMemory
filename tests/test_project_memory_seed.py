from __future__ import annotations

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
    assert result.behavior_instructions_written == 0
    assert [item.name for item in result.skills] == ["Memory population discipline"]
    assert [item.name for item in result.playbooks] == ["Neutral memory bootstrap"]
    assert {item.name for item in result.concepts} == {
        "workspace_id",
        "memory candidate review",
        "memory snapshot",
        "memory integrity audit",
    }

    assert _count(applied_conn, "agent_roles", "project-x") == 0
    assert _count(applied_conn, "behavior_instructions", "project-x") == 0
    assert _count(applied_conn, "agent_skills", "project-x") == 1
    assert _count(applied_conn, "agent_playbooks", "project-x") == 1
    assert _count(applied_conn, "domain_concepts", "project-x") == 4


def test_neutral_project_memory_seed_is_idempotent(applied_conn: sqlite3.Connection) -> None:
    first = seed_neutral_project_memory(applied_conn, workspace_id="project-x")
    second = seed_neutral_project_memory(applied_conn, workspace_id="project-x")

    assert first.skills[0].id == second.skills[0].id
    assert first.playbooks[0].id == second.playbooks[0].id
    assert {item.id for item in first.concepts} == {item.id for item in second.concepts}
    assert _count(applied_conn, "agent_skills", "project-x") == 1
    assert _count(applied_conn, "agent_playbooks", "project-x") == 1
    assert _count(applied_conn, "domain_concepts", "project-x") == 4
    assert _count(applied_conn, "behavior_instructions", "project-x") == 0
