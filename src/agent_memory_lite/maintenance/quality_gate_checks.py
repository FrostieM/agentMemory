"""Per-table check helpers for the quality gate.

Behavior-instruction checks live in ``quality_gate_behavior.py``.
Theory / experiment / decision checks live here.
"""

from __future__ import annotations

import sqlite3

from agent_memory_lite.maintenance.quality_gate_behavior import behavior_instruction_findings
from agent_memory_lite.maintenance.quality_gate_models import (
    QualityGateFinding,
    json_empty,
    table_exists,
)

__all__ = [
    "behavior_instruction_findings",
    "strict_decision_findings",
    "strict_experiment_findings",
    "strict_theory_findings",
]


def strict_theory_findings(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[QualityGateFinding]:
    if not table_exists(conn, "theories"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, status, validation_criteria_json, experiment_plan,
               evidence_count, importance
        FROM theories
        WHERE workspace_id = ?
          AND status IN ('proposed', 'testing', 'supported', 'validated', 'rejected')
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[QualityGateFinding] = []
    for row in rows:
        status = str(row["status"])
        evidence_count = int(row["evidence_count"])
        lacks_validation = (
            json_empty(row["validation_criteria_json"]) and not row["experiment_plan"]
        )
        if status != "rejected" and lacks_validation:
            findings.append(
                QualityGateFinding(
                    kind="theory_not_testable",
                    severity="error",
                    target_type="theory",
                    target_id=str(row["id"]),
                    summary="Active theory lacks validation criteria and experiment plan.",
                    details={
                        "title": row["title"],
                        "status": status,
                        "importance": row["importance"],
                    },
                )
            )
        if status in {"validated", "rejected"} and evidence_count == 0:
            findings.append(
                QualityGateFinding(
                    kind="terminal_theory_without_evidence",
                    severity="error",
                    target_type="theory",
                    target_id=str(row["id"]),
                    summary="Validated or rejected theory must keep supporting/refuting evidence.",
                    details={
                        "title": row["title"],
                        "status": status,
                        "evidence_count": evidence_count,
                    },
                )
            )
    return findings


def strict_experiment_findings(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[QualityGateFinding]:
    if not table_exists(conn, "research_experiments"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, status, priority, success_criteria_json, hypothesis
        FROM research_experiments
        WHERE workspace_id = ?
          AND status IN ('planned', 'running', 'blocked')
          AND priority >= 0.8
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[QualityGateFinding] = []
    for row in rows:
        if json_empty(row["success_criteria_json"]):
            findings.append(
                QualityGateFinding(
                    kind="important_experiment_without_success_criteria",
                    severity="error",
                    target_type="experiment",
                    target_id=str(row["id"]),
                    summary="Important open experiment lacks explicit success criteria.",
                    details={
                        "title": row["title"],
                        "status": row["status"],
                        "priority": row["priority"],
                    },
                )
            )
        if not str(row["hypothesis"]).strip():
            findings.append(
                QualityGateFinding(
                    kind="important_experiment_without_hypothesis",
                    severity="error",
                    target_type="experiment",
                    target_id=str(row["id"]),
                    summary="Important open experiment lacks a falsifiable hypothesis.",
                    details={"title": row["title"], "priority": row["priority"]},
                )
            )
    return findings


def strict_decision_findings(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[QualityGateFinding]:
    if not table_exists(conn, "decisions"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, rationale, source_episode_id, importance
        FROM decisions
        WHERE workspace_id = ? AND status = 'active' AND importance >= 0.8
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[QualityGateFinding] = []
    for row in rows:
        if not row["rationale"] and not row["source_episode_id"]:
            findings.append(
                QualityGateFinding(
                    kind="important_decision_without_provenance",
                    severity="error",
                    target_type="decision",
                    target_id=str(row["id"]),
                    summary="Important active decision lacks both rationale and source episode.",
                    details={"title": row["title"], "importance": row["importance"]},
                )
            )
    return findings
