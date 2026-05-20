"""Read-only memory retrieval integrity audit.

Per-check helpers live in
``integrity_db_checks.py`` (sqlite / manifest / pollution / stray-db),
``integrity_fts.py`` (FTS + retrieval round-trip),
``integrity_vector.py`` (vector-store coverage + metadata),
``integrity_hygiene_checks.py`` (maintenance / capability links /
candidates / research hygiene / hygiene_report).

Result types and small helpers live in ``integrity_models.py``;
repair-hint translation lives in ``integrity_repair_hints.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_lite.fts.chunks_fts import rebuild_chunks_fts
from agent_memory_lite.maintenance.integrity_db_checks import (
    sqlite_check,
    stray_db_check,
    workspace_manifest_check,
    workspace_pollution_check,
)
from agent_memory_lite.maintenance.integrity_fts import fts_check, roundtrip_check
from agent_memory_lite.maintenance.integrity_hygiene_checks import (
    candidate_hygiene_check,
    capability_links_check,
    hygiene_report_check,
    maintenance_check,
    research_hygiene_check,
)
from agent_memory_lite.maintenance.integrity_models import (
    IntegrityCheck,
    IntegrityReport,
)
from agent_memory_lite.maintenance.integrity_repair_hints import (
    collect_counts,
    collect_repair_hints,
)
from agent_memory_lite.maintenance.integrity_vector import vector_check
from agent_memory_lite.vector_store.base import VectorStore

__all__ = ["IntegrityCheck", "IntegrityReport", "repair_fts", "run_integrity_audit"]


def run_integrity_audit(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    vector_store: VectorStore | None = None,
    db_path: str | Path | None = None,
    expected_provider_name: str | None = None,
    expected_vector_backend: str | None = None,
) -> IntegrityReport:
    checks = {
        "sqlite": sqlite_check(conn),
        "workspace_manifest": workspace_manifest_check(conn, workspace_id),
        "workspace_pollution": workspace_pollution_check(
            conn,
            workspace_id,
            db_path=Path(db_path) if db_path is not None else None,
        ),
        "fts": fts_check(conn, workspace_id),
        "vector": vector_check(
            conn,
            workspace_id,
            vector_store,
            expected_provider_name=expected_provider_name,
            expected_vector_backend=expected_vector_backend,
        ),
        "retrieval_roundtrip": roundtrip_check(conn, workspace_id),
        "maintenance_events": maintenance_check(conn, workspace_id),
        "capability_links": capability_links_check(conn, workspace_id),
        "candidate_hygiene": candidate_hygiene_check(conn, workspace_id),
        "research_hygiene": research_hygiene_check(conn, workspace_id),
        "hygiene": hygiene_report_check(conn, workspace_id),
        "stray_dbs": stray_db_check(Path(db_path) if db_path is not None else None),
    }
    failures = [name for name, check in checks.items() if check.status == "degraded"]
    warnings = [name for name, check in checks.items() if check.status == "warning"]
    unknown = [name for name, check in checks.items() if check.status == "unknown"]
    if failures:
        status = "degraded"
    elif warnings:
        status = "warning"
    elif unknown:
        status = "unknown"
    else:
        status = "ok"

    return IntegrityReport(
        status=status,
        workspace_id=workspace_id,
        checks=checks,
        counts=collect_counts(checks),
        failures=failures,
        warnings=warnings,
        repair_hints=collect_repair_hints(checks),
    )


def repair_fts(conn: sqlite3.Connection, *, workspace_id: str) -> int:
    return rebuild_chunks_fts(conn, workspace_id=workspace_id)
