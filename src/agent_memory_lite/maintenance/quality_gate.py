"""Strict content-quality gate for research memory.

Result types + small probes live in ``quality_gate_models.py``;
per-table check helpers live in ``quality_gate_checks.py``.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.maintenance.quality_gate_checks import (
    behavior_instruction_findings,
    strict_decision_findings,
    strict_experiment_findings,
    strict_theory_findings,
)
from agent_memory_lite.maintenance.quality_gate_models import (
    QualityGateFinding,
    QualityGateReport,
    aggregate_counts,
)
from agent_memory_lite.utils.time import iso_now

__all__ = ["QualityGateFinding", "QualityGateReport", "run_quality_gate"]


def run_quality_gate(conn: sqlite3.Connection, *, workspace_id: str) -> QualityGateReport:
    hygiene = run_hygiene_report(conn, workspace_id=workspace_id)
    findings: list[QualityGateFinding] = []
    findings.extend(strict_theory_findings(conn, workspace_id=workspace_id))
    findings.extend(strict_experiment_findings(conn, workspace_id=workspace_id))
    findings.extend(strict_decision_findings(conn, workspace_id=workspace_id))
    findings.extend(behavior_instruction_findings(conn, workspace_id=workspace_id))
    for finding in hygiene.findings:
        if finding.kind == "missing_capability_link":
            findings.append(
                QualityGateFinding(
                    kind=finding.kind,
                    severity="warning",
                    target_type=finding.target_type,
                    target_id=finding.target_id,
                    summary=finding.summary,
                    details=finding.details,
                )
            )
    counts = aggregate_counts(findings)
    if counts.get("severity_error", 0):
        status = "degraded"
    elif findings:
        status = "warning"
    else:
        status = "ok"
    return QualityGateReport(
        status=status,
        workspace_id=workspace_id,
        generated_at=iso_now(),
        counts=counts,
        findings=findings,
    )
