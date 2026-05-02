"""Operator-focused review queue.

A short, action-oriented queue of memory rows that need an operator
decision right now. Distinct from ``run_hygiene_report`` (broad
content-quality findings) and ``run_quality_gate`` (research-trust
gate): this surface returns concrete tasks the operator can click
on — promote / reject / archive / resolve / supersede — rather
than a list of weak-quality findings.

Each queue item carries the suggested action so the UI / agent
knows which endpoint to call.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from agent_memory_lite.models.enums import (
    MaintenanceEventStatus,
    MaintenanceSeverity,
    MemoryCandidateStatus,
)
from agent_memory_lite.repositories.candidates_repo import list_candidates
from agent_memory_lite.repositories.maintenance_repo import list_maintenance_events
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class ReviewQueueItem:
    action: str  # e.g. "promote_candidate" / "resolve_maintenance_event"
    target_type: str
    target_id: str
    summary: str
    severity: str
    created_at: str
    details: dict[str, Any] = field(default_factory=dict)


def _candidate_items(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int
) -> list[ReviewQueueItem]:
    rows = list_candidates(
        conn,
        workspace_id=workspace_id,
        statuses=[MemoryCandidateStatus.NEW],
        limit=limit,
    )
    out: list[ReviewQueueItem] = []
    for row in rows:
        out.append(
            ReviewQueueItem(
                action="promote_candidate",
                target_type="candidate",
                target_id=row.id,
                summary=f"Review candidate {row.kind.value}: {row.subject[:60]}",
                severity="info",
                created_at=row.created_at,
                details={
                    "kind": row.kind.value,
                    "trust_level": row.trust_level.value,
                    "confidence": row.confidence,
                },
            )
        )
    return out


def _maintenance_items(
    conn: sqlite3.Connection, *, workspace_id: str, limit: int
) -> list[ReviewQueueItem]:
    rows = list_maintenance_events(
        conn,
        workspace_id=workspace_id,
        statuses=[MaintenanceEventStatus.OPEN],
        limit=limit,
    )
    severity_priority = {
        MaintenanceSeverity.CRITICAL: "critical",
        MaintenanceSeverity.ERROR: "error",
        MaintenanceSeverity.WARNING: "warning",
        MaintenanceSeverity.INFO: "info",
    }
    out: list[ReviewQueueItem] = []
    for row in rows:
        out.append(
            ReviewQueueItem(
                action="resolve_maintenance_event",
                target_type=row.target_type or "maintenance_event",
                target_id=row.id,
                summary=row.summary[:120],
                severity=severity_priority.get(row.severity, "info"),
                created_at=row.created_at,
                details={"kind": row.kind, "details": row.details},
            )
        )
    return out


def build_review_queue(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit_per_kind: int = 10,
) -> dict[str, Any]:
    candidates = _candidate_items(conn, workspace_id=workspace_id, limit=limit_per_kind)
    maintenance = _maintenance_items(conn, workspace_id=workspace_id, limit=limit_per_kind)
    items = candidates + maintenance
    # Severity-priority ordering: critical > error > warning > info
    severity_rank = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    items.sort(key=lambda item: (severity_rank.get(item.severity, 9), item.created_at))
    return {
        "workspace_id": workspace_id,
        "generated_at": iso_now(),
        "counts": {
            "candidates": len(candidates),
            "maintenance_events": len(maintenance),
            "total": len(items),
        },
        "items": [
            {
                "action": item.action,
                "target_type": item.target_type,
                "target_id": item.target_id,
                "summary": item.summary,
                "severity": item.severity,
                "created_at": item.created_at,
                "details": item.details,
            }
            for item in items
        ],
    }
