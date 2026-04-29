from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.capability_writer import upsert_agent_role
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.maintenance.auto_triage import triage_capability_links
from agent_memory_lite.models.capabilities import AgentRoleIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import CapabilityLinkTargetType
from agent_memory_lite.repositories.capability_links_repo import list_capability_links


def _seed_unlinked_decision(applied_conn: sqlite3.Connection) -> str:
    upsert_agent_role(
        applied_conn,
        AgentRoleIn(
            workspace_id="project-a",
            name="Runtime operator",
            purpose="Own PM2 deploy safety, API watchdog recovery, and runtime backpressure.",
            responsibilities=[
                "Review API health watchdog decisions",
                "Handle PM2 deploy recovery and backpressure incidents",
            ],
            confidence=0.95,
        ),
    )
    decision = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="project-a",
            title="Throttle discovery during API watchdog backpressure",
            decision_text=(
                "Discovery pauses when API watchdog detects PM2 runtime backpressure "
                "during deploy recovery."
            ),
            rationale="Backpressure protects the runtime from event-loop wedges.",
            importance=0.95,
        ),
    )
    return decision.id


def test_auto_triage_dry_run_does_not_write_links(applied_conn: sqlite3.Connection) -> None:
    decision_id = _seed_unlinked_decision(applied_conn)

    result = triage_capability_links(applied_conn, workspace_id="project-a")

    assert result.dry_run is True
    assert result.status == "improved"
    assert result.applied_links
    assert result.applied_links[0].target_id == decision_id
    assert result.applied_links[0].link_id is None
    links = list_capability_links(
        applied_conn,
        workspace_id="project-a",
        target_type=CapabilityLinkTargetType.DECISION,
        target_id=decision_id,
    )
    assert links == []


def test_auto_triage_apply_links_and_clears_capability_warning(
    applied_conn: sqlite3.Connection,
) -> None:
    decision_id = _seed_unlinked_decision(applied_conn)

    result = triage_capability_links(applied_conn, workspace_id="project-a", apply=True)

    assert result.dry_run is False
    assert result.status == "ok"
    assert result.before_counts["missing_capability_link"] == 1
    assert result.after_counts["total_findings"] == 0
    assert result.applied_links[0].link_id is not None
    links = list_capability_links(
        applied_conn,
        workspace_id="project-a",
        target_type=CapabilityLinkTargetType.DECISION,
        target_id=decision_id,
    )
    assert len(links) == 1
    assert links[0].capability_name == "Runtime operator"
    assert links[0].relation.value == "implementation_role"
