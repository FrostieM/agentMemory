"""Persist hygiene findings as recurrence-aware maintenance_events (v1.9).

Each finding flows through ``upsert_finding_event`` so an open event for
the same (kind, target_type, target_id) gets its recurrence counter
incremented instead of producing a brand-new row each scan.

A single ``hygiene.finding_persisted`` audit row covers the whole batch.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent_memory_lite.maintenance.hygiene_models import HygieneFinding
from agent_memory_lite.maintenance.recurrence_detector import upsert_finding_event
from agent_memory_lite.repositories.audit_repo import insert_audit
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class HygienePersistStats:
    inserted: int
    incremented: int
    threshold_crossings: int


def persist_findings(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    findings: list[HygieneFinding],
    threshold: int,
) -> HygienePersistStats:
    if not findings:
        return HygienePersistStats(inserted=0, incremented=0, threshold_crossings=0)
    inserted = 0
    incremented = 0
    crossings = 0
    for finding in findings:
        result = upsert_finding_event(
            conn,
            workspace_id=workspace_id,
            kind=finding.kind,
            severity=finding.severity,
            summary=finding.summary,
            details=finding.details,
            target_type=finding.target_type,
            target_id=finding.target_id,
            threshold=threshold,
        )
        if result.is_new:
            inserted += 1
        else:
            incremented += 1
        if result.crossed_threshold:
            crossings += 1
    insert_audit(
        conn,
        workspace_id=workspace_id,
        action="hygiene.finding_persisted",
        target_type="workspace",
        target_id=workspace_id,
        after={
            "inserted": inserted,
            "incremented": incremented,
            "threshold_crossings": crossings,
            "total_findings": len(findings),
            "at": iso_now(),
        },
    )
    return HygienePersistStats(
        inserted=inserted, incremented=incremented, threshold_crossings=crossings
    )
