"""Workspace manifest guard.

The manifest is a one-row contract inside a memory database. It prevents a
process configured for one workspace from silently serving another workspace's
SQLite file after paths or environment variables drift.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from agent_memory_lite.models.workspace_manifest import WorkspaceManifest
from agent_memory_lite.utils.time import iso_now


class WorkspaceManifestError(RuntimeError):
    """Raised when the database workspace contract is unsafe."""


def _json_dict(raw: str | None) -> dict[str, Any]:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


def get_workspace_manifest(conn: sqlite3.Connection) -> WorkspaceManifest | None:
    if not _table_exists(conn, "workspace_manifest"):
        return None
    row = conn.execute("SELECT * FROM workspace_manifest WHERE id = 1").fetchone()
    if row is None:
        return None
    data = dict(row)
    return WorkspaceManifest(
        workspace_id=data["workspace_id"],
        db_uuid=data["db_uuid"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        last_audit_at=data["last_audit_at"],
        last_audit_status=data["last_audit_status"],
        last_repair_at=data.get("last_repair_at"),
        metadata=_json_dict(data["metadata_json"]),
    )


def _workspace_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'virtual table') AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    tables: list[str] = []
    for row in rows:
        table = str(row[0])
        try:
            cols = [str(col[1]) for col in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        except sqlite3.OperationalError:
            continue
        if "workspace_id" in cols:
            tables.append(table)
    return tables


def infer_workspace_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _workspace_tables(conn):
        rows = conn.execute(
            f"""
            SELECT workspace_id, COUNT(*) AS n
            FROM {table}
            GROUP BY workspace_id
            """
        ).fetchall()
        for row in rows:
            workspace_id = str(row["workspace_id"])
            counts[workspace_id] = counts.get(workspace_id, 0) + int(row["n"])
    return counts


def _insert_manifest(conn: sqlite3.Connection, *, workspace_id: str) -> WorkspaceManifest:
    now = iso_now()
    db_uuid = f"db_{uuid4().hex}"
    metadata = {"created_by": "workspace_manifest_guard"}
    conn.execute(
        """
        INSERT INTO workspace_manifest (
            id, workspace_id, db_uuid, created_at, updated_at, metadata_json
        ) VALUES (1, ?, ?, ?, ?, ?)
        """,
        (workspace_id, db_uuid, now, now, json.dumps(metadata, sort_keys=True)),
    )
    return WorkspaceManifest(
        workspace_id=workspace_id,
        db_uuid=db_uuid,
        created_at=now,
        updated_at=now,
        last_audit_at=None,
        last_audit_status=None,
        last_repair_at=None,
        metadata=metadata,
    )


def ensure_workspace_manifest(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    allow_default_workspace: bool,
) -> WorkspaceManifest:
    """Create or validate the manifest for this database.

    First-run creation is intentionally tolerant of historical `default` rows
    so old project DBs can be migrated. It is strict about non-default rows that
    point to another workspace because that is strong evidence of a wrong DB.
    """
    if not _table_exists(conn, "workspace_manifest"):
        raise WorkspaceManifestError("workspace_manifest table missing; run migrations first")
    if not allow_default_workspace and workspace_id == "default":
        raise WorkspaceManifestError(
            "workspace_id='default' is disabled; configure MEMORY_WORKSPACE_ID explicitly"
        )
    manifest = get_workspace_manifest(conn)
    if manifest is not None:
        if manifest.workspace_id != workspace_id:
            raise WorkspaceManifestError(
                "workspace manifest mismatch: "
                f"db belongs to {manifest.workspace_id!r}, process is {workspace_id!r}"
            )
        return manifest

    inferred = infer_workspace_row_counts(conn)
    non_default = {key for key in inferred if key != "default"}
    if non_default and non_default != {workspace_id}:
        raise WorkspaceManifestError(
            "cannot create workspace manifest: existing rows belong to "
            f"{sorted(non_default)!r}, process is {workspace_id!r}"
        )
    return _insert_manifest(conn, workspace_id=workspace_id)


def update_workspace_manifest_audit(
    conn: sqlite3.Connection,
    *,
    status: str,
    audited_at: str | None = None,
) -> None:
    if not _table_exists(conn, "workspace_manifest"):
        return
    conn.execute(
        """
        UPDATE workspace_manifest
        SET last_audit_at = ?, last_audit_status = ?, updated_at = ?
        WHERE id = 1
        """,
        (audited_at or iso_now(), status, iso_now()),
    )


def update_workspace_manifest_repair(
    conn: sqlite3.Connection,
    *,
    repaired_at: str | None = None,
) -> None:
    if not _table_exists(conn, "workspace_manifest"):
        return
    cols = [str(col[1]) for col in conn.execute("PRAGMA table_info(workspace_manifest)")]
    if "last_repair_at" not in cols:
        return
    timestamp = repaired_at or iso_now()
    conn.execute(
        """
        UPDATE workspace_manifest
        SET last_repair_at = ?, updated_at = ?
        WHERE id = 1
        """,
        (timestamp, iso_now()),
    )
