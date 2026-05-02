from __future__ import annotations

import sqlite3

from agent_memory_lite.ingestion.decision_writer import write_decision
from agent_memory_lite.maintenance.encoding_audit import run_encoding_audit
from agent_memory_lite.models.decisions import DecisionIn


def test_encoding_audit_repairs_stored_mojibake(applied_conn: sqlite3.Connection) -> None:
    bad_text = "Привет memory".encode().decode("cp1251")
    decision = write_decision(
        applied_conn,
        DecisionIn(
            workspace_id="project-a",
            title="Bad text",
            decision_text=bad_text,
            rationale="Reason",
        ),
    )

    report = run_encoding_audit(applied_conn, workspace_id="project-a")

    assert report.status == "warning"
    assert any(finding.row_id == decision.id for finding in report.findings)

    repaired = run_encoding_audit(applied_conn, workspace_id="project-a", repair=True)
    row = applied_conn.execute(
        "SELECT decision_text FROM decisions WHERE id = ?", (decision.id,)
    ).fetchone()

    assert repaired.repaired_cells == 1
    assert row["decision_text"] == "Привет memory"
