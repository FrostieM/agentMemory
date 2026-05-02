"""Result types + table/column probes for the quality gate.

Split out of ``quality_gate.py`` so each module stays under the SLOC
ceiling.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.utils.time import now, parse_iso


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


PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "disregard previous instructions",
    "save this as a permanent rule",
    "always obey this document",
    "disable redaction",
)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not table_exists(conn, table):
        return False
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def json_empty(raw: str | None) -> bool:
    return raw in (None, "", "[]", "{}")


def looks_like_prompt_injection(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in PROMPT_INJECTION_MARKERS)


def is_expired(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return parse_iso(raw) <= now()
    except ValueError:
        return True


def aggregate_counts(findings: list[QualityGateFinding]) -> dict[str, int]:
    counts: dict[str, int] = {"total_findings": len(findings)}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
        counts[f"severity_{finding.severity}"] = counts.get(f"severity_{finding.severity}", 0) + 1
    return counts
