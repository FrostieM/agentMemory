"""Detailed memory hygiene report.

Integrity checks answer whether retrieval is mechanically consistent. Hygiene
checks answer whether the memory remains scientifically useful: open candidates
must be triaged, theories must be testable, experiments must close, insights
must be linked, and important objects should be influenced by roles/skills.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class HygieneFinding:
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
class HygieneReport:
    status: str
    workspace_id: str
    generated_at: str
    counts: dict[str, int]
    findings: list[HygieneFinding]

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


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_len(raw: str | None) -> int:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return 0
    if isinstance(data, list | dict):
        return len(data)
    return 0


def _find_stale_candidates(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    stale_days: int,
) -> list[HygieneFinding]:
    if not _table_exists(conn, "memory_candidates"):
        return []
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    rows = conn.execute(
        """
        SELECT id, kind, subject, updated_at
        FROM memory_candidates
        WHERE workspace_id = ? AND status = 'new'
        ORDER BY updated_at
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    for row in rows:
        updated = _parse_iso(str(row["updated_at"]))
        if updated is None or updated >= cutoff:
            continue
        findings.append(
            HygieneFinding(
                kind="stale_candidate",
                severity="warning",
                target_type="memory_candidate",
                target_id=str(row["id"]),
                summary="Candidate remained new past the review window.",
                details={
                    "candidate_kind": row["kind"],
                    "subject": row["subject"],
                    "updated_at": row["updated_at"],
                    "stale_days": stale_days,
                },
            )
        )
    return findings


def _find_theory_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not _table_exists(conn, "theories"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, status, validation_criteria_json, experiment_plan,
               evidence_count, importance
        FROM theories
        WHERE workspace_id = ?
          AND status IN ('proposed', 'testing', 'supported', 'validated', 'rejected')
        ORDER BY importance DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    for row in rows:
        theory_id = str(row["id"])
        title = str(row["title"])
        status = str(row["status"])
        validation_count = _json_len(row["validation_criteria_json"])
        if status != "rejected" and validation_count == 0 and not row["experiment_plan"]:
            findings.append(
                HygieneFinding(
                    kind="theory_without_validation",
                    severity="warning",
                    target_type="theory",
                    target_id=theory_id,
                    summary="Active theory has no validation criteria or experiment plan.",
                    details={"title": title, "status": status, "importance": row["importance"]},
                )
            )
        if int(row["evidence_count"]) == 0:
            findings.append(
                HygieneFinding(
                    kind="theory_without_evidence",
                    severity="warning",
                    target_type="theory",
                    target_id=theory_id,
                    summary="Theory has no attached evidence yet.",
                    details={"title": title, "status": status, "importance": row["importance"]},
                )
            )
    return findings


def _find_experiment_gaps(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    stale_days: int,
) -> list[HygieneFinding]:
    if not _table_exists(conn, "research_experiments"):
        return []
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    rows = conn.execute(
        """
        SELECT id, title, status, priority, due_at, updated_at, success_criteria_json
        FROM research_experiments
        WHERE workspace_id = ? AND status IN ('planned', 'running', 'blocked')
        ORDER BY priority DESC, updated_at
        """,
        (workspace_id,),
    ).fetchall()
    findings: list[HygieneFinding] = []
    now = datetime.now(UTC)
    for row in rows:
        experiment_id = str(row["id"])
        title = str(row["title"])
        due_at = _parse_iso(row["due_at"])
        updated_at = _parse_iso(str(row["updated_at"]))
        if due_at is not None and due_at < now:
            findings.append(
                HygieneFinding(
                    kind="overdue_experiment",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment is past due.",
                    details={"title": title, "status": row["status"], "due_at": row["due_at"]},
                )
            )
        if updated_at is not None and updated_at < cutoff:
            findings.append(
                HygieneFinding(
                    kind="stale_open_experiment",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment has not been updated within the hygiene window.",
                    details={
                        "title": title,
                        "status": row["status"],
                        "updated_at": row["updated_at"],
                        "stale_days": stale_days,
                    },
                )
            )
        if _json_len(row["success_criteria_json"]) == 0:
            findings.append(
                HygieneFinding(
                    kind="experiment_without_success_criteria",
                    severity="warning",
                    target_type="experiment",
                    target_id=experiment_id,
                    summary="Open experiment has no explicit success criteria.",
                    details={"title": title, "status": row["status"], "priority": row["priority"]},
                )
            )
    return findings


def _find_insight_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not _table_exists(conn, "research_insights"):
        return []
    rows = conn.execute(
        """
        SELECT id, insight_type, summary, status, target_type, target_id
        FROM research_insights
        WHERE workspace_id = ? AND status IN ('new', 'accepted')
          AND (COALESCE(target_type, '') = '' OR COALESCE(target_id, '') = '')
        ORDER BY updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [
        HygieneFinding(
            kind="unlinked_insight",
            severity="warning",
            target_type="research_insight",
            target_id=str(row["id"]),
            summary="Active insight is not linked to a target object.",
            details={
                "insight_type": row["insight_type"],
                "status": row["status"],
                "summary": row["summary"],
            },
        )
        for row in rows
    ]


def _find_decision_gaps(conn: sqlite3.Connection, *, workspace_id: str) -> list[HygieneFinding]:
    if not _table_exists(conn, "decisions"):
        return []
    rows = conn.execute(
        """
        SELECT id, title, rationale, source_episode_id, importance
        FROM decisions
        WHERE workspace_id = ? AND status = 'active' AND importance >= 0.8
          AND COALESCE(rationale, '') = ''
          AND COALESCE(source_episode_id, '') = ''
        ORDER BY importance DESC, updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [
        HygieneFinding(
            kind="weak_decision_provenance",
            severity="warning",
            target_type="decision",
            target_id=str(row["id"]),
            summary="Important active decision lacks both rationale and source episode.",
            details={
                "title": row["title"],
                "has_rationale": bool(row["rationale"]),
                "has_source_episode": bool(row["source_episode_id"]),
                "importance": row["importance"],
            },
        )
        for row in rows
    ]


def _find_unlinked_capability_targets(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    importance_threshold: float,
) -> list[HygieneFinding]:
    if not _table_exists(conn, "capability_links"):
        return []
    specs = [
        (
            "theory",
            "theories",
            "id, title AS label, importance, status",
            "importance >= ? AND status IN ('proposed', 'testing', 'supported', 'validated', 'rejected')",
            "importance",
        ),
        (
            "experiment",
            "research_experiments",
            "id, title AS label, priority AS importance, status",
            "priority >= ? AND status IN ('planned', 'running', 'blocked')",
            "priority",
        ),
        (
            "decision",
            "decisions",
            "id, title AS label, importance, status",
            "importance >= ? AND status = 'active'",
            "importance",
        ),
    ]
    findings: list[HygieneFinding] = []
    for target_type, table, columns, where_sql, score_field in specs:
        if not _table_exists(conn, table):
            continue
        rows = conn.execute(
            f"""
            SELECT {columns}
            FROM {table} t
            WHERE t.workspace_id = ?
              AND {where_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM capability_links l
                WHERE l.workspace_id = t.workspace_id
                  AND l.target_type = ?
                  AND l.target_id = t.id
              )
            ORDER BY {score_field} DESC
            """,
            (workspace_id, importance_threshold, target_type),
        ).fetchall()
        for row in rows:
            findings.append(
                HygieneFinding(
                    kind="missing_capability_link",
                    severity="warning",
                    target_type=target_type,
                    target_id=str(row["id"]),
                    summary="Important object has no role/skill/playbook influence link.",
                    details={
                        "label": row["label"],
                        "status": row["status"],
                        "importance": row["importance"],
                    },
                )
            )
    return findings


def _count_by_kind(findings: list[HygieneFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    return counts


def run_hygiene_report(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    candidate_stale_days: int = 14,
    experiment_stale_days: int = 30,
    importance_threshold: float = 0.8,
) -> HygieneReport:
    findings: list[HygieneFinding] = []
    findings.extend(
        _find_stale_candidates(conn, workspace_id=workspace_id, stale_days=candidate_stale_days)
    )
    findings.extend(_find_theory_gaps(conn, workspace_id=workspace_id))
    findings.extend(
        _find_experiment_gaps(
            conn,
            workspace_id=workspace_id,
            stale_days=experiment_stale_days,
        )
    )
    findings.extend(_find_insight_gaps(conn, workspace_id=workspace_id))
    findings.extend(_find_decision_gaps(conn, workspace_id=workspace_id))
    findings.extend(
        _find_unlinked_capability_targets(
            conn,
            workspace_id=workspace_id,
            importance_threshold=importance_threshold,
        )
    )
    counts = _count_by_kind(findings)
    counts["total_findings"] = len(findings)
    status = "warning" if findings else "ok"
    return HygieneReport(
        status=status,
        workspace_id=workspace_id,
        generated_at=iso_now(),
        counts=counts,
        findings=findings,
    )
