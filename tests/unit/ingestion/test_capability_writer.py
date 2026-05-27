from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.models.capabilities import AgentPlaybookIn, AgentRoleIn, AgentSkillIn
from agent_memory_lite.repositories.capabilities_repo import build_agent_capabilities


def test_capability_upserts_reuse_name_per_workspace(applied_conn: sqlite3.Connection) -> None:
    role = upsert_agent_role(
        applied_conn,
        AgentRoleIn(
            workspace_id="default",
            name="Runtime operator",
            purpose="Validate runtime health before changing data.",
            responsibilities=["Check health endpoints", "Report exact blockers"],
            boundaries=["Do not reset data without approval"],
            tools=["/memory/brief", "/health"],
            confidence=0.8,
        ),
    )
    updated_role = upsert_agent_role(
        applied_conn,
        AgentRoleIn(
            workspace_id="default",
            name="Runtime operator",
            purpose="Validate runtime health and preserve evidence before recovery.",
            responsibilities=["Check health endpoints", "Preserve evidence"],
            confidence=0.9,
        ),
    )

    assert updated_role.id == role.id
    assert updated_role.confidence == 0.9
    assert updated_role.responsibilities == ["Check health endpoints", "Preserve evidence"]


def test_agent_capabilities_rank_relevant_roles_skills_playbooks(
    applied_conn: sqlite3.Connection,
) -> None:
    upsert_agent_role(
        applied_conn,
        AgentRoleIn(
            workspace_id="default",
            name="Research analyst",
            purpose="Turn snapshots into evidence-backed insights.",
            responsibilities=["Run cohort analysis", "Record experiment results"],
            confidence=0.85,
        ),
    )
    upsert_agent_skill(
        applied_conn,
        AgentSkillIn(
            workspace_id="default",
            name="Snapshot research",
            summary="Inspect preserved database snapshots and produce quantified findings.",
            when_to_use=["A dataset has been copied before destructive maintenance"],
            inputs=["SQLite path", "DuckDB path"],
            outputs=["Metrics", "Insight candidates"],
            related_roles=["Research analyst"],
            confidence=0.9,
        ),
    )
    upsert_agent_playbook(
        applied_conn,
        AgentPlaybookIn(
            workspace_id="default",
            name="Pre-reset research snapshot",
            goal="Preserve and verify data before resetting a live system.",
            triggers=["User asks for cleanup or reset"],
            steps=["Copy the database", "Verify integrity", "Register the snapshot"],
            success_criteria=["Snapshot is retrievable in memory"],
            required_skills=["Snapshot research"],
            confidence=0.95,
        ),
    )

    capabilities = build_agent_capabilities(
        applied_conn,
        workspace_id="default",
        query="snapshot research reset",
        limit=5,
    )

    assert capabilities.roles[0].name == "Research analyst"
    assert capabilities.skills[0].name == "Snapshot research"
    assert capabilities.playbooks[0].name == "Pre-reset research snapshot"
