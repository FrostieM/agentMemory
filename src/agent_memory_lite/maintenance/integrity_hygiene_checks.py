"""Maintenance + capability + candidate hygiene checks for the integrity audit.

Research-side checks (research_hygiene_check + hygiene_report_check)
live in ``integrity_research_check.py``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from agent_memory_lite.maintenance.integrity_models import (
    IntegrityCheck,
    count_query,
    parse_iso,
    table_exists,
)
from agent_memory_lite.maintenance.integrity_research_check import (
    hygiene_report_check,
    research_hygiene_check,
)
from agent_memory_lite.repositories.capability_links_repo import CAPABILITY_TABLES, TARGET_TABLES
from agent_memory_lite.repositories.maintenance_repo import count_open_maintenance_events

__all__ = [
    "candidate_hygiene_check",
    "capability_links_check",
    "hygiene_report_check",
    "maintenance_check",
    "research_hygiene_check",
]


def maintenance_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not table_exists(conn, "maintenance_events"):
        return IntegrityCheck(status="unknown", details={"reason": "maintenance_events missing"})
    open_events = count_open_maintenance_events(conn, workspace_id=workspace_id)
    return IntegrityCheck(
        status="ok" if open_events == 0 else "degraded",
        details={"open_events": open_events},
    )


def capability_links_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not table_exists(conn, "capability_links"):
        return IntegrityCheck(status="unknown", details={"reason": "capability_links missing"})

    total = count_query(
        conn,
        "SELECT COUNT(*) FROM capability_links WHERE workspace_id = ?",
        (workspace_id,),
    )

    # Target tables come from the canonical TARGET_TABLES map so a new
    # link target (e.g. Phase 4's plan_step) is audited without keeping a
    # second hardcoded copy in sync. A table the DB lacks yet is skipped
    # -- the LEFT JOIN would otherwise crash on a pre-migration database.
    missing_targets: dict[str, int] = {}
    for target_type, table in TARGET_TABLES.items():
        if not table_exists(conn, table):
            continue
        count = count_query(
            conn,
            f"""
            SELECT COUNT(*)
            FROM capability_links l
            LEFT JOIN {table} t
              ON t.id = l.target_id AND t.workspace_id = l.workspace_id
            WHERE l.workspace_id = ?
              AND l.target_type = ?
              AND t.id IS NULL
            """,
            (workspace_id, target_type.value),
        )
        if count:
            missing_targets[target_type.value] = count

    # Capability tables likewise come from the canonical CAPABILITY_TABLES
    # map; the table_exists guard skips the v2-named agent_* tables on a
    # canonical-schema DB that does not have them.
    missing_capabilities: dict[str, int] = {}
    for capability_type, table in CAPABILITY_TABLES.items():
        if not table_exists(conn, table):
            continue
        count = count_query(
            conn,
            f"""
            SELECT COUNT(*)
            FROM capability_links l
            LEFT JOIN {table} c
              ON c.id = l.capability_id AND c.workspace_id = l.workspace_id
             AND c.subtype = ?
            WHERE l.workspace_id = ?
              AND l.capability_type = ?
              AND c.id IS NULL
            """,
            (capability_type.value, workspace_id, capability_type.value),
        )
        if count:
            missing_capabilities[capability_type.value] = count

    status = "ok" if not missing_targets and not missing_capabilities else "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "links": total,
            "missing_targets": missing_targets,
            "missing_capabilities": missing_capabilities,
        },
    )


def candidate_hygiene_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not table_exists(conn, "candidates"):
        return IntegrityCheck(status="unknown", details={"reason": "candidates missing"})

    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM candidates
        WHERE workspace_id = ?
        GROUP BY status
        """,
        (workspace_id,),
    ).fetchall()
    by_status = {str(row["status"]): int(row["n"]) for row in rows}
    cutoff = datetime.now(UTC) - timedelta(days=14)
    stale_new = 0
    stale_rows = conn.execute(
        """
        SELECT updated_at
        FROM candidates
        WHERE workspace_id = ? AND status = 'new'
        """,
        (workspace_id,),
    ).fetchall()
    for row in stale_rows:
        updated = parse_iso(str(row["updated_at"]))
        if updated is not None and updated < cutoff:
            stale_new += 1
    status = "ok" if stale_new == 0 else "warning"
    return IntegrityCheck(
        status=status,
        details={
            "by_status": by_status,
            "new_candidates": by_status.get("new", 0),
            "stale_new_older_than_days": 14,
            "stale_new": stale_new,
        },
    )
