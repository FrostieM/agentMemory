"""Detect and safely quarantine cross-workspace rows.

Internal helpers (table walk, count, sample, quarantine) live in
``workspace_doctor_internals.py``. This module owns the wire-shape
report and the orchestrator entry point.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_memory_lite.maintenance.workspace_doctor_internals import (
    WorkspacePollutionRow,
    sample_rows,
    workspace_counts,
)
from agent_memory_lite.maintenance.workspace_doctor_quarantine import (
    quarantine_rows,
    write_quarantine,
)

__all__ = [
    "WorkspaceDoctorReport",
    "WorkspacePollutionRow",
    "run_workspace_doctor",
]


@dataclass(frozen=True, slots=True)
class WorkspaceDoctorReport:
    status: str
    workspace_id: str
    counts_before: dict[str, dict[str, int]]
    counts_after: dict[str, dict[str, int]]
    samples: list[WorkspacePollutionRow] = field(default_factory=list)
    protected_tables: list[str] = field(default_factory=list)
    quarantined_rows: dict[str, int] = field(default_factory=dict)
    quarantine_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_id": self.workspace_id,
            "counts_before": self.counts_before,
            "counts_after": self.counts_after,
            "samples": [row.to_dict() for row in self.samples],
            "protected_tables": self.protected_tables,
            "quarantined_rows": self.quarantined_rows,
            "quarantine_path": self.quarantine_path,
        }


def run_workspace_doctor(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    include_default: bool = False,
    quarantine: bool = False,
    quarantine_path: Path | None = None,
    sample_limit: int = 5,
) -> WorkspaceDoctorReport:
    """Return workspace pollution details and optionally quarantine offending rows.

    `quarantine=True` exports matching rows to JSON and deletes them from the DB.
    The caller is responsible for making a database backup first.
    """

    if quarantine and quarantine_path is None:
        raise ValueError("quarantine_path is required when quarantine=True")

    before = workspace_counts(conn, workspace_id=workspace_id)
    samples = sample_rows(conn, workspace_id=workspace_id, limit_per_table=sample_limit)
    protected_tables = [table for table in before if table == "workspace_manifest"]
    deleted: dict[str, int] = {}
    path_text: str | None = None
    if quarantine:
        assert quarantine_path is not None
        write_quarantine(
            samples,
            workspace_id=workspace_id,
            include_default=include_default,
            quarantine_path=quarantine_path,
        )
        path_text = str(quarantine_path)
        deleted = quarantine_rows(
            conn,
            workspace_id=workspace_id,
            include_default=include_default,
        )

    after = workspace_counts(conn, workspace_id=workspace_id)
    unresolved = {table: rows for table, rows in after.items() if table not in protected_tables}
    status = "ok" if not unresolved else "degraded"
    if protected_tables:
        status = "degraded"
    return WorkspaceDoctorReport(
        status=status,
        workspace_id=workspace_id,
        counts_before=before,
        counts_after=after,
        samples=samples,
        protected_tables=protected_tables,
        quarantined_rows=deleted,
        quarantine_path=path_text,
    )
