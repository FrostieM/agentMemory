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
    # 1.2.3: seed wrote one capability-link discipline rule.
    # 1.2.4: added second rule — search-before-write discipline.
    # All seeded BIs must be project-AGNOSTIC (no language, personality,
    # or project-specific behavior). Project-specific rules remain
    # operator-driven via memory_upsert_behavior_instruction.
    assert result.behavior_instructions_written == 2
    assert [item.name for item in result.skills] == ["Memory population discipline"]
    assert [item.name for item in result.playbooks] == ["Neutral memory bootstrap"]
    assert {item.name for item in result.concepts} == {
        "workspace_id",
        "memory candidate review",
        "memory snapshot",
        "memory integrity audit",
    }
    assert {item.name for item in result.behavior_instructions} == {
        "Link capability after every decision and theory write",
        "Search before write — auto-inject is not exhaustive",
    }

    assert _count(applied_conn, "agent_roles", "project-x") == 0
    assert _count(applied_conn, "behavior_instructions", "project-x") == 2
    assert _count(applied_conn, "agent_skills", "project-x") == 1
    assert _count(applied_conn, "agent_playbooks", "project-x") == 1
    assert _count(applied_conn, "domain_concepts", "project-x") == 4


def test_neutral_project_memory_seed_is_idempotent(applied_conn: sqlite3.Connection) -> None:
    first = seed_neutral_project_memory(applied_conn, workspace_id="project-x")
    second = seed_neutral_project_memory(applied_conn, workspace_id="project-x")

    assert first.skills[0].id == second.skills[0].id
    assert first.playbooks[0].id == second.playbooks[0].id
    assert {item.id for item in first.concepts} == {item.id for item in second.concepts}
    # Behavior_instruction upsert is also idempotent on (workspace_id, name).
    # All N seeded BIs must round-trip with stable ids on re-seed.
    first_bi_ids = {item.id for item in first.behavior_instructions}
    second_bi_ids = {item.id for item in second.behavior_instructions}
    assert first_bi_ids == second_bi_ids
    assert _count(applied_conn, "agent_skills", "project-x") == 1
    assert _count(applied_conn, "agent_playbooks", "project-x") == 1
    assert _count(applied_conn, "domain_concepts", "project-x") == 4
    assert _count(applied_conn, "behavior_instructions", "project-x") == 2


def test_seed_behavior_instruction_metadata(applied_conn: sqlite3.Connection) -> None:
    """1.2.3+: every seeded discipline rule must carry the right enum
    values and metadata so it's immediately visible in
    <behavior_instructions> of the next memory_get_context envelope.

    All BIs from DISCIPLINE_FACTORIES must share the same baseline
    (operating_rule + workspace + user_preference + current_user_wins +
    seed_bootstrap source_type) so an operator's explicit instruction
    in the same chat always overrides them."""
    seed_neutral_project_memory(applied_conn, workspace_id="project-x")
    rows = applied_conn.execute(
        "SELECT name, kind, scope, priority, conflict_policy, source_type, "
        "active, applies_to_json FROM behavior_instructions "
        "WHERE workspace_id='project-x' ORDER BY name"
    ).fetchall()
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert names == {
        "Link capability after every decision and theory write",
        "Search before write — auto-inject is not exhaustive",
    }
    # Every seed BI must share the canonical baseline metadata
    for row in rows:
        assert row["kind"] == "operating_rule", row["name"]
        assert row["scope"] == "workspace", row["name"]
        assert row["priority"] == "user_preference", row["name"]
        assert row["conflict_policy"] == "current_user_wins", row["name"]
        assert row["source_type"] == "seed_bootstrap", row["name"]
        assert row["active"] in (1, True), row["name"]

    # The capability-link rule applies_to research-mutating APIs
    cap_link = next(
        r for r in rows if r["name"] == "Link capability after every decision and theory write"
    )
    cap_applies = json.loads(cap_link["applies_to_json"] or "[]")
    assert "memory_write_decision" in cap_applies
    assert "memory_write_theory" in cap_applies

    # The search-first rule applies_to writes that should be preceded by search
    search_rule = next(
        r for r in rows if r["name"] == "Search before write — auto-inject is not exhaustive"
    )
    search_applies = json.loads(search_rule["applies_to_json"] or "[]")
    assert "memory_write_decision" in search_applies
    assert "before architectural decisions" in search_applies
