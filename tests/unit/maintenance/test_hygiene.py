from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.ingestion.theory_writer import write_theory
from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.models.decisions import DecisionIn
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
