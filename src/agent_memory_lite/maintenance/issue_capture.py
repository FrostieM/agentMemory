"""Auto-capture audit / hygiene findings as issues (plan step 9.4).

Turns the automated quality signals the system already produces -- hygiene
findings and integrity-audit failures -- into first-class ``issue`` rows so
the debt/risk registry fills itself. Each finding above a severity floor
becomes an open issue; signature dedup (step 9.3) means a finding that
recurs on every audit run does not pile up duplicates.

Decoupled by design: it duck-types the finding (object attrs OR plain dict)
on ``severity`` / ``summary`` / ``kind`` / ``target_type`` / ``target_id`` /
``details`` so it never imports the high-impact hygiene/integrity hubs and
stays trivially testable.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_memory_lite.ingestion.issue_writer import write_issue_canonical

# Source severities (hygiene + integrity) -> issue severity.
_SEVERITY_MAP = {
    "critical": "critical",
    "error": "major",
    "degraded": "major",
    "warning": "major",
    "info": "minor",
    "ok": "minor",
}
_RANK = {"minor": 0, "major": 1, "critical": 2}


def _field(finding: Any, name: str, default: Any = None) -> Any:
    if isinstance(finding, dict):
        return finding.get(name, default)
    return getattr(finding, name, default)


def _to_issue_severity(raw: Any) -> str:
    return _SEVERITY_MAP.get(str(raw or "").lower(), "minor")


def capture_findings_as_issues(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    findings: Any,
    source: str = "audit",
    category: str = "risk",
    min_severity: str = "major",
) -> int:
    """Write each finding at/above ``min_severity`` as a deduped issue.

    Returns the number of findings that produced or matched an issue row
    (dedup hits included). A finding below the floor is skipped so the
    registry is not flooded with minor/info noise.
    """
    floor = _RANK.get(min_severity, 1)
    captured = 0
    for finding in findings or []:
        severity = _to_issue_severity(_field(finding, "severity"))
        if _RANK[severity] < floor:
            continue
        title = str(_field(finding, "summary") or _field(finding, "kind") or "issue").strip()
        if not title:
            continue
        target_type = _field(finding, "target_type")
        target_id = _field(finding, "target_id")
        evidence = [f"{target_type}:{target_id}"] if target_type and target_id else []
        details = _field(finding, "details")
        payload = {
            "title": title[:200],
            "category": category,
            "severity": severity,
            "status": "open",
            "source": source,
            "description": (json.dumps(details, default=str) if details else "")[:2000],
            "evidence_json": json.dumps(evidence),
        }
        if write_issue_canonical(conn, workspace_id=workspace_id, payload=payload) is not None:
            captured += 1
    return captured
