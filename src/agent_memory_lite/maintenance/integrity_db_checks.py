"""SQLite + workspace + stray-db checks for the integrity audit."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_lite.maintenance.integrity_db_checks_pollution import (
    _registered_workspace_ids,
    workspace_pollution_check,
)
from agent_memory_lite.maintenance.integrity_models import (
    IntegrityCheck,
    table_exists,
)
from agent_memory_lite.repositories.workspace_manifest_repo import get_workspace_manifest

__all__ = [
    "_registered_workspace_ids",
    "sqlite_check",
    "stray_db_check",
    "workspace_manifest_check",
    "workspace_pollution_check",
]


def sqlite_check(conn: sqlite3.Connection) -> IntegrityCheck:
    # v3.5 sector-5 audit-followup: ``PRAGMA integrity_check`` returns
    # the rows it has and can in theory return an empty result on a
    # mid-corruption read. ``fetchone()[0]`` would raise TypeError on
    # None and the whole audit would abort (no other checks would
    # run). Treat an empty / None response as "unknown" → degraded.
    integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
    integrity = str(integrity_row[0]) if integrity_row else "unknown"
    quick_row = conn.execute("PRAGMA quick_check").fetchone()
    quick = str(quick_row[0]) if quick_row else "unknown"
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
