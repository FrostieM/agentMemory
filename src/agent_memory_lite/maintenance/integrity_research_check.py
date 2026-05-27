"""Research-side hygiene checks for the integrity audit."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.maintenance.integrity_models import (
    IntegrityCheck,
    count_query,
    parse_iso,
    table_exists,
)


def research_hygiene_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    required = ("theories", "experiments", "insights")
    missing = [table for table in required if not table_exists(conn, table)]
    if missing:
        return IntegrityCheck(status="unknown", details={"missing_tables": missing})

    undisciplined_theories = count_query(
        conn,
        """
        SELECT COUNT(*)
        FROM theories
        WHERE workspace_id = ?
          AND status IN ('proposed', 'testing', 'supported')
          AND COALESCE(experiment_plan, '') = ''
          AND COALESCE(validation_criteria_json, '[]') IN ('[]', '')
        """,
        (workspace_id,),
    )
    rejected_without_evidence = count_query(
        conn,
        """
        SELECT COUNT(*)
        FROM theories
        WHERE workspace_id = ?
          AND status = 'rejected'
          AND evidence_count = 0
        """,
        (workspace_id,),
    )

    open_experiment_rows = conn.execute(
        """
        SELECT updated_at
        FROM experiments
        WHERE workspace_id = ? AND status IN ('planned', 'running', 'blocked')
        """,
        (workspace_id,),
    ).fetchall()
    cutoff = datetime.now(UTC) - timedelta(days=30)
    stale_open_experiments = 0
    for row in open_experiment_rows:
        updated = parse_iso(str(row["updated_at"]))
        if updated is not None and updated < cutoff:
            stale_open_experiments += 1

    warning = (
        undisciplined_theories > 0 or rejected_without_evidence > 0 or stale_open_experiments > 0
    )
    return IntegrityCheck(
        status="warning" if warning else "ok",
        details={
            "undisciplined_active_theories": undisciplined_theories,
            "rejected_theories_without_evidence": rejected_without_evidence,
            "stale_open_experiments_older_than_days": 30,
            "stale_open_experiments": stale_open_experiments,
        },
    )


def hygiene_report_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    report = run_hygiene_report(conn, workspace_id=workspace_id)
    capability_link_warnings = [
        finding.to_dict()
        for finding in report.findings
        if finding.kind == "missing_capability_link"
    ]
    return IntegrityCheck(
        status=report.status,
        details={
            "counts": report.counts,
            "total_findings": report.counts.get("total_findings", 0),
            "capability_link_warnings": capability_link_warnings[:20],
            "findings_sample": [finding.to_dict() for finding in report.findings[:20]],
        },
    )
