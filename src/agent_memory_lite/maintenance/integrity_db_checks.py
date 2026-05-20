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


def _registered_workspace_ids(db_path: Path | None) -> set[str]:
    """Return workspace_ids that the hub registry knows about and that
    point at the SAME db file as ``db_path``.

    Used by ``workspace_pollution_check`` to distinguish "rogue data
    leaked into this DB" (the original bug it was designed to catch)
    from "this DB is a legitimate hub serving multiple registered
    workspaces" (the v3.0+ design where one ``memory.db`` backs
    several project workspaces under hub mode).

    Returns an empty set on any registry-read error so the check
    degrades to the conservative pre-v3.5 behaviour (flag everything
    foreign) instead of silently allowing real pollution.
    """
    if db_path is None:
        return set()
    try:
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415
        from agent_memory_lite.config.workspace_registry import (  # noqa: PLC0415
            WorkspaceRegistry,
        )
    except ImportError:
        return set()
    try:
        settings = get_settings()
        registry = WorkspaceRegistry(settings.workspaces_file)
        entries = registry.list()
    except Exception:
        return set()
    target = db_path.resolve()
    matched: set[str] = set()
    for entry in entries:
        try:
            entry_path = Path(entry.db_path).resolve()
        except (OSError, ValueError):
            continue
        if entry_path == target:
            matched.add(entry.id)
    return matched


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


def workspace_pollution_check(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    db_path: Path | None = None,
) -> IntegrityCheck:
    """Detect rows whose ``workspace_id`` is neither the target nor any
    workspace registered against the same DB file.

    Prior to v3.5 this flagged ANY foreign workspace_id as degraded,
    which produced a constant false-positive on hub-mode setups where
    one ``memory.db`` legitimately serves multiple registered
    workspaces (the recommended v3.0+ deployment). The pollution
    signal that matters is unregistered foreign ids — those indicate
    cross-DB leakage from a script that wrote to the wrong file.

    Registered foreign ids surface in
    ``details.registered_foreign_rows`` for transparency but no
    longer raise ``status='degraded'``.
    """
    registered = _registered_workspace_ids(db_path) - {workspace_id}
    default_rows: dict[str, int] = {}
    registered_foreign_rows: dict[str, int] = {}
    unregistered_foreign_rows: dict[str, int] = {}
    for table in workspace_tables(conn):
        default_count = count_query(
            conn,
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id = 'default'",
            (),
        )
        if default_count:
            default_rows[table] = default_count
        # Split foreign rows into "registered hub mate" vs "unregistered
        # stray". Only the latter signals actual data leakage.
        foreign_total = count_query(
            conn,
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id NOT IN (?, 'default')",
            (workspace_id,),
        )
        if foreign_total == 0:
            continue
        if not registered:
            unregistered_foreign_rows[table] = foreign_total
            continue
        placeholders = ",".join("?" * len(registered))
        registered_count = count_query(
            conn,
            f"SELECT COUNT(*) FROM {table} WHERE workspace_id IN ({placeholders})",
            tuple(registered),
        )
        if registered_count:
            registered_foreign_rows[table] = registered_count
        unregistered_count = foreign_total - registered_count
        if unregistered_count:
            unregistered_foreign_rows[table] = unregistered_count
    status = "ok"
    # `default` rows on a non-default target are always a problem — they
    # come from a writer that forgot to pass workspace_id. Same for
    # unregistered foreign ids (something wrote to the wrong DB).
    if (workspace_id != "default" and default_rows) or unregistered_foreign_rows:
        status = "degraded"
    return IntegrityCheck(
        status=status,
        details={
            "default_rows": default_rows,
            "registered_foreign_rows": registered_foreign_rows,
            "unregistered_foreign_rows": unregistered_foreign_rows,
            # Legacy key kept so existing report consumers / JSON
            # snapshots don't break. Mirrors the union of registered +
            # unregistered foreign rows.
            "other_workspace_rows": {
                **registered_foreign_rows,
                **unregistered_foreign_rows,
            },
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
