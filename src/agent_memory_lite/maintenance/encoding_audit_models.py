"""Result types for the encoding-audit pass.

Split out of ``encoding_audit.py`` so the scan/repair logic stays under
the SLOC ceiling and the wire-shape dataclasses are reusable by tools
that read the audit report (CLI scripts, dashboard, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EncodingFinding:
    table: str
    row_id: str
    column: str
    issue: str
    before_sample: str
    after_sample: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "row_id": self.row_id,
            "column": self.column,
            "issue": self.issue,
            "before_sample": self.before_sample,
            "after_sample": self.after_sample,
        }


@dataclass(frozen=True, slots=True)
class EncodingAuditReport:
    status: str
    workspace_id: str
    findings: list[EncodingFinding] = field(default_factory=list)
    repaired_cells: int = 0
    scanned_cells: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "scanned_cells": self.scanned_cells,
            "repaired_cells": self.repaired_cells,
            "findings_count": len(self.findings),
            "findings": [finding.to_dict() for finding in self.findings],
            "warnings": list(self.warnings),
        }
