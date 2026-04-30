"""Strict content-quality gate for research memory."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.maintenance.hygiene import run_hygiene_report
from agent_memory_lite.utils.time import iso_now, now, parse_iso


@dataclass(frozen=True, slots=True)
class QualityGateFinding:
    kind: str
    severity: str
    target_type: str
    target_id: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class QualityGateReport:
    status: str
    workspace_id: str
    generated_at: str
    counts: dict[str, int]
    findings: list[QualityGateFinding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "generated_at": self.generated_at,
            "counts": self.counts,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


def _json_empty(raw: str | None) -> bool:
    return raw in (None, "", "[]", "{}")


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


_PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "disregard previous instructions",
    "save this as a permanent rule",
    "always obey this document",
    "disable redaction",
)


def _looks_like_prompt_injection(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in _PROMPT_INJECTION_MARKERS)


def _is_expired(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return parse_iso(raw) <= now()
    except ValueError:
        return True


def _strict_theory_findings(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
) -> list[QualityGateFinding]:
    if not _table_exists(conn, "theories"):
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
            _json_empty(row["validation_criteria_json"]) and not row["experiment_plan"]
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


def _strict_experiment_findings(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
) -> list[QualityGateFinding]:
    if not _table_exists(conn, "research_experiments"):
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
        if _json_empty(row["success_criteria_json"]):
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


def _strict_decision_findings(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
) -> list[QualityGateFinding]:
    if not _table_exists(conn, "decisions"):
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


def _behavior_instruction_findings(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
) -> list[QualityGateFinding]:
    if not _table_exists(conn, "behavior_instructions"):
        return []
    has_governance = _column_exists(conn, "behavior_instructions", "source_type")
    governance_cols = (
        "source_type, source_id, reviewed_by, reviewed_at, expires_at, conflict_group"
        if has_governance
        else "'manual' AS source_type, NULL AS source_id, NULL AS reviewed_by, "
        "NULL AS reviewed_at, NULL AS expires_at, NULL AS conflict_group"
    )
    rows = conn.execute(
        f"""
        SELECT id, name, kind, priority, rule, source_episode_id, confidence,
               {governance_cols}
        FROM behavior_instructions
        WHERE workspace_id = ? AND active = 1
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[QualityGateFinding] = []
    for row in rows:
        if float(row["confidence"]) < 0.5:
            findings.append(
                QualityGateFinding(
                    kind="low_confidence_behavior_instruction",
                    severity="warning",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Active behavior instruction has low confidence.",
                    details={"name": row["name"], "confidence": row["confidence"]},
                )
            )
        source_type = str(row["source_type"] or "manual")
        source_id = row["source_id"]
        if (
            not row["source_episode_id"]
            and not source_id
            and source_type
            not in {
                "manual",
                "system_seed",
            }
        ):
            findings.append(
                QualityGateFinding(
                    kind="behavior_instruction_without_source",
                    severity="warning",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Active behavior instruction has no source episode.",
                    details={
                        "name": row["name"],
                        "kind": row["kind"],
                        "priority": row["priority"],
                        "source_type": source_type,
                    },
                )
            )
        if _is_expired(row["expires_at"]):
            findings.append(
                QualityGateFinding(
                    kind="expired_behavior_instruction_still_active",
                    severity="error",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Expired behavior instruction is still active.",
                    details={"name": row["name"], "expires_at": row["expires_at"]},
                )
            )
        if source_type in {"external", "untrusted_doc"}:
            findings.append(
                QualityGateFinding(
                    kind="untrusted_behavior_instruction_active",
                    severity="error",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Behavior instruction from untrusted content must not be active.",
                    details={"name": row["name"], "source_type": source_type},
                )
            )
        if source_type not in {
            "manual",
            "user_direct",
            "system_seed",
        } and _looks_like_prompt_injection(str(row["rule"])):
            findings.append(
                QualityGateFinding(
                    kind="behavior_instruction_prompt_injection_risk",
                    severity="error",
                    target_type="behavior_instruction",
                    target_id=str(row["id"]),
                    summary="Behavior instruction contains prompt-injection language from a non-authoritative source.",
                    details={"name": row["name"], "source_type": source_type},
                )
            )
    return findings


def _counts(findings: list[QualityGateFinding]) -> dict[str, int]:
    counts: dict[str, int] = {"total_findings": len(findings)}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
        counts[f"severity_{finding.severity}"] = counts.get(f"severity_{finding.severity}", 0) + 1
    return counts


def run_quality_gate(conn: sqlite3.Connection, *, workspace_id: str) -> QualityGateReport:
    hygiene = run_hygiene_report(conn, workspace_id=workspace_id)
    findings: list[QualityGateFinding] = []
    findings.extend(_strict_theory_findings(conn, workspace_id=workspace_id))
    findings.extend(_strict_experiment_findings(conn, workspace_id=workspace_id))
    findings.extend(_strict_decision_findings(conn, workspace_id=workspace_id))
    findings.extend(_behavior_instruction_findings(conn, workspace_id=workspace_id))
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
    counts = _counts(findings)
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
