from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.capability_writer import (
    upsert_agent_playbook,
    upsert_agent_role,
    upsert_agent_skill,
)
from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.episode_pipeline import ingest_episode
from agent_memory_lite.ingestion.research_writer import write_experiment
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.models.capabilities import AgentPlaybookIn, AgentRoleIn, AgentSkillIn
from agent_memory_lite.models.decisions import DecisionIn
from agent_memory_lite.models.enums import EpisodeSource
from agent_memory_lite.models.episodes import EpisodeIn
from agent_memory_lite.models.research import ExperimentIn
from agent_memory_lite.models.theories import TheoryIn


def test_hygiene_reports_theory_and_capability_gaps(
    applied_conn: sqlite3.Connection,
) -> None:
    theory = write_theory(
        applied_conn,
        TheoryIn(
            workspace_id="project-a",
            title="Unvalidated alpha hypothesis",
            claim="The model may have an edge that needs testing.",
            status="testing",
            importance=0.95,
        ),
    )

    report = run_hygiene_report(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    kinds = {finding.kind for finding in report.findings}
    assert "theory_without_validation" in kinds
    assert "theory_without_evidence" in kinds
    assert "missing_capability_link" in kinds
    assert any(finding.target_id == theory.id for finding in report.findings)


def test_hygiene_reports_weak_decision_provenance(
    applied_conn: sqlite3.Connection,
) -> None:
    decision = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="project-a",
            title="Important architecture decision",
            decision_text="Use the new pipeline.",
            importance=0.95,
        ),
    )

    report = run_hygiene_report(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    assert any(
        finding.kind == "weak_decision_provenance" and finding.target_id == decision.id
        for finding in report.findings
    )


def test_hygiene_accepts_decision_with_rationale_or_source(
    applied_conn: sqlite3.Connection,
) -> None:
    source = ingest_episode(
        applied_conn,
        EpisodeIn(
            workspace_id="project-a",
            source_type=EpisodeSource.AGENT_ACTION,
            raw_text="Observed source evidence.",
        ),
    )
    with_rationale = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="project-a",
            title="Decision with rationale",
            decision_text="Use the new pipeline.",
            rationale="It is simpler to audit.",
            importance=0.95,
        ),
    )
    with_source = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="project-a",
            title="Decision with source",
            decision_text="Use the existing service.",
            source_episode_id=source.episode.id,
            importance=0.95,
        ),
    )

    report = run_hygiene_report(applied_conn, workspace_id="project-a")

    weak_ids = {
        finding.target_id
        for finding in report.findings
        if finding.kind == "weak_decision_provenance"
    }
    assert with_rationale.id not in weak_ids
    assert with_source.id not in weak_ids


def test_hygiene_accepts_object_success_criteria(
    applied_conn: sqlite3.Connection,
) -> None:
    experiment = write_experiment(
        applied_conn,
        ExperimentIn(
            workspace_id="project-a",
            title="Replay gate policy",
            hypothesis="A replay should prove whether the gate policy is useful.",
            success_criteria={"min_recall": 0.8, "max_drawdown": 0.05},
            priority=0.95,
        ),
    )

    report = run_hygiene_report(applied_conn, workspace_id="project-a")

    assert not any(
        finding.kind == "experiment_without_success_criteria" and finding.target_id == experiment.id
        for finding in report.findings
    )


def test_hygiene_suggests_capability_links_for_unlinked_decision(
    applied_conn: sqlite3.Connection,
) -> None:
    role = upsert_agent_role(
        applied_conn,
        AgentRoleIn(
            workspace_id="project-a",
            name="Runtime Reliability Architect",
            purpose="Reviews API watchdogs, PM2 deploy safety, and event-loop pressure.",
            responsibilities=[
                "Design API health watchdog policy",
                "Review deploy and backpressure decisions",
            ],
            confidence=0.9,
        ),
    )
    skill = upsert_agent_skill(
        applied_conn,
        AgentSkillIn(
            workspace_id="project-a",
            name="API watchdog diagnostics",
            summary="Diagnose API health, PM2 restarts, event-loop pressure, and backpressure.",
            when_to_use=["API health warning", "PM2 deploy issue", "event-loop pressure"],
            confidence=0.95,
        ),
    )
    upsert_agent_playbook(
        applied_conn,
        AgentPlaybookIn(
            workspace_id="project-a",
            name="Deploy health watchdog review",
            goal="Verify API health and PM2 deploy behavior before trusting runtime state.",
            steps=["Check health endpoint", "Inspect PM2 status", "Review backpressure"],
            confidence=0.8,
        ),
    )
    decision = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="project-a",
            title="Throttle discovery when API backpressure is active",
            decision_text=(
                "Discovery must pause when the API watchdog observes event-loop pressure "
                "during PM2 deploy recovery."
            ),
            rationale="Backpressure protects live API health.",
            importance=0.95,
        ),
    )

    report = run_hygiene_report(applied_conn, workspace_id="project-a")

    finding = next(
        item
        for item in report.findings
        if item.kind == "missing_capability_link" and item.target_id == decision.id
    )
    suggestions = finding.details["suggested_capability_links"]
    assert suggestions
    assert any(
        item["capability_type"] == "skill"
        and item["capability_id"] == skill.id
        and item["relation"] == "method"
        for item in suggestions
    )
    assert any(
        item["capability_type"] == "role"
        and item["capability_id"] == role.id
        and item["relation"] == "implementation_role"
        for item in suggestions
    )
    assert all(item["target_id"] == decision.id for item in suggestions)
    assert all(item["matched_terms"] for item in suggestions)
