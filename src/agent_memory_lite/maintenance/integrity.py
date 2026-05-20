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
    # v3.5 sector-5 audit-followup: per-check try/except so one
    # exception in any check (e.g. sqlite_check's PRAGMA fetchone[0]
    # blowing up on a corrupt DB, or workspace_pollution_check
    # OperationalError on a poisoned schema) cannot abort the entire
    # audit. Each check now degrades to a typed "error" entry that
    # the operator can see in the report.
    from collections.abc import Callable  # noqa: PLC0415

    db_path_obj = Path(db_path) if db_path is not None else None
    _check_specs: tuple[tuple[str, Callable[[], IntegrityCheck]], ...] = (
        ("sqlite", lambda: sqlite_check(conn)),
        ("workspace_manifest", lambda: workspace_manifest_check(conn, workspace_id)),
        (
            "workspace_pollution",
            lambda: workspace_pollution_check(conn, workspace_id, db_path=db_path_obj),
        ),
        ("fts", lambda: fts_check(conn, workspace_id)),
        (
            "vector",
            lambda: vector_check(
                conn,
                workspace_id,
                vector_store,
                expected_provider_name=expected_provider_name,
                expected_vector_backend=expected_vector_backend,
            ),
        ),
        ("retrieval_roundtrip", lambda: roundtrip_check(conn, workspace_id)),
        ("maintenance_events", lambda: maintenance_check(conn, workspace_id)),
        ("capability_links", lambda: capability_links_check(conn, workspace_id)),
        ("candidate_hygiene", lambda: candidate_hygiene_check(conn, workspace_id)),
        ("research_hygiene", lambda: research_hygiene_check(conn, workspace_id)),
        ("hygiene", lambda: hygiene_report_check(conn, workspace_id)),
        ("stray_dbs", lambda: stray_db_check(db_path_obj)),
    )
    checks: dict[str, IntegrityCheck] = {}
    for name, runner in _check_specs:
        try:
            checks[name] = runner()
        except Exception as exc:  # noqa: BLE001 — per-check isolation
            checks[name] = IntegrityCheck(
                status="degraded",
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
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
