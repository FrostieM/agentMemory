"""SQLite + workspace + stray-db checks for the integrity audit."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_lite.maintenance.integrity_models import (
    IntegrityCheck,
    count_query,
    table_exists,
    workspace_tables,
)
from agent_memory_lite.repositories.workspace_manifest_repo import get_workspace_manifest


def sqlite_check(conn: sqlite3.Connection) -> IntegrityCheck:
    integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    status = "ok" if integrity == "ok" and quick == "ok" and not fk_rows else "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "integrity_check": integrity,
            "quick_check": quick,
            "foreign_key_violations": len(fk_rows),
        },
    )


def workspace_manifest_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    if not table_exists(conn, "workspace_manifest"):
        return IntegrityCheck(status="degraded", details={"error": "workspace_manifest missing"})
    manifest = get_workspace_manifest(conn)
    if manifest is None:
        return IntegrityCheck(status="warning", details={"error": "workspace_manifest empty"})
    status = "ok" if manifest.workspace_id == workspace_id else "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "workspace_id": manifest.workspace_id,
            "expected_workspace_id": workspace_id,
            "db_uuid": manifest.db_uuid,
            "last_audit_at": manifest.last_audit_at,
            "last_audit_status": manifest.last_audit_status,
            "last_repair_at": manifest.last_repair_at,
        },
    )


def workspace_pollution_check(conn: sqlite3.Connection, workspace_id: str) -> IntegrityCheck:
    default_rows: dict[str, int] = {}
    other_rows: dict[str, int] = {}
    for table in workspace_tables(conn):
        default_count = count_query(
            conn,
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id = 'default'",
            (),
        )
        if default_count:
            default_rows[table] = default_count
        other_count = count_query(
            conn,
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id NOT IN (?, 'default')",
            (workspace_id,),
        )
        if other_count:
            other_rows[table] = other_count
    status = "ok"
    if (workspace_id != "default" and default_rows) or other_rows:
        status = "degraded"
    return IntegrityCheck(
        status=status,
        details={"default_rows": default_rows, "other_workspace_rows": other_rows},
    )


def stray_db_check(db_path: Path | None) -> IntegrityCheck:
    if db_path is None:
        return IntegrityCheck(status="unknown", details={"reason": "db_path_not_supplied"})
    resolved = db_path.resolve()
    if resolved.name != "memory.db" or resolved.parent.name != ".agent_memory":
        return IntegrityCheck(
            status="ok",
            details={"reason": "non_standard_db_path", "db_path": str(resolved)},
        )
    project_root = resolved.parent.parent
    candidates: list[str] = []
    try:
        for candidate in project_root.rglob(".agent_memory/memory.db"):
            candidate_resolved = candidate.resolve()
            if candidate_resolved != resolved:
                candidates.append(str(candidate_resolved))
    except OSError as exc:
        return IntegrityCheck(
            status="unknown",
            details={"db_path": str(resolved), "error": str(exc)},
        )
    return IntegrityCheck(
        status="warning" if candidates else "ok",
        details={
            "db_path": str(resolved),
            "project_root": str(project_root),
            "stray_dbs": candidates,
        },
    )
