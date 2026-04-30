"""Detect and safely quarantine cross-workspace rows."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_memory_lite.repositories.workspace_manifest_repo import (
    update_workspace_manifest_repair,
)
from agent_memory_lite.utils.time import iso_now


@dataclass(frozen=True, slots=True)
class WorkspacePollutionRow:
    table: str
    workspace_id: str
    row_identity: str
    row: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "workspace_id": self.workspace_id,
            "row_identity": self.row_identity,
            "row": self.row,
        }


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


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'virtual table')",
        (table,),
    ).fetchone()
    return row is not None


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
            cols = [
                str(col[1]) for col in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")
            ]
        except sqlite3.OperationalError:
            continue
        if "workspace_id" in cols:
            tables.append(table)
    return tables


def _pollution_condition() -> str:
    return "workspace_id IS NOT NULL AND workspace_id <> ?"


def _quarantine_condition(*, include_default: bool) -> str:
    if include_default:
        return _pollution_condition()
    return "workspace_id IS NOT NULL AND workspace_id NOT IN (?, 'default')"


def _workspace_counts(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
) -> dict[str, dict[str, int]]:
    condition = _pollution_condition()
    counts: dict[str, dict[str, int]] = {}
    for table in _workspace_tables(conn):
        rows = conn.execute(
            f"""
            SELECT workspace_id, COUNT(*) AS n
            FROM {_quote_ident(table)}
            WHERE {condition}
            GROUP BY workspace_id
            ORDER BY workspace_id
            """,
            (workspace_id,),
        ).fetchall()
        if rows:
            counts[table] = {str(row["workspace_id"]): int(row["n"]) for row in rows}
    return counts


def _row_identity(row: sqlite3.Row) -> str:
    keys = set(row.keys())
    for key in ("id", "chunk_id", "episode_id", "target_id", "task_id"):
        if key in keys and row[key] is not None:
            return str(row[key])
    return "<unknown>"


def _sample_rows(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    limit_per_table: int,
) -> list[WorkspacePollutionRow]:
    condition = _pollution_condition()
    samples: list[WorkspacePollutionRow] = []
    for table in _workspace_tables(conn):
        rows = conn.execute(
            f"""
            SELECT *
            FROM {_quote_ident(table)}
            WHERE {condition}
            ORDER BY workspace_id
            LIMIT ?
            """,
            (workspace_id, limit_per_table),
        ).fetchall()
        for row in rows:
            samples.append(
                WorkspacePollutionRow(
                    table=table,
                    workspace_id=str(row["workspace_id"]),
                    row_identity=_row_identity(row),
                    row=dict(row),
                )
            )
    return samples


def _child_first_tables(conn: sqlite3.Connection, tables: list[str]) -> list[str]:
    table_set = set(tables)
    parents: dict[str, set[str]] = {table: set() for table in tables}
    for table in tables:
        try:
            fk_rows = conn.execute(f"PRAGMA foreign_key_list({_quote_ident(table)})").fetchall()
        except sqlite3.OperationalError:
            continue
        for fk in fk_rows:
            parent = str(fk["table"] if isinstance(fk, sqlite3.Row) else fk[2])
            if parent in table_set:
                parents[table].add(parent)

    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(table: str) -> None:
        if table in visited:
            return
        if table in visiting:
            ordered.append(table)
            visited.add(table)
            return
        visiting.add(table)
        children = [candidate for candidate, refs in parents.items() if table in refs]
        for child in sorted(children):
            visit(child)
        visiting.remove(table)
        if table not in visited:
            visited.add(table)
            ordered.append(table)

    for table in sorted(tables):
        visit(table)
    return list(dict.fromkeys(ordered))


def _write_quarantine(
    rows: list[WorkspacePollutionRow],
    *,
    workspace_id: str,
    include_default: bool,
    quarantine_path: Path,
) -> None:
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": iso_now(),
        "workspace_id": workspace_id,
        "include_default": include_default,
        "rows": [row.to_dict() for row in rows],
    }
    quarantine_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _quarantine_rows(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    include_default: bool,
) -> dict[str, int]:
    condition = _quarantine_condition(include_default=include_default)
    protected = {"workspace_manifest"}
    tables = [table for table in _workspace_tables(conn) if table not in protected]
    deleted: dict[str, int] = {}
    with conn:
        for table in _child_first_tables(conn, tables):
            cursor = conn.execute(
                f"DELETE FROM {_quote_ident(table)} WHERE {condition}",
                (workspace_id,),
            )
            changed = max(int(cursor.rowcount if cursor.rowcount is not None else 0), 0)
            if changed:
                deleted[table] = changed
        if deleted and _table_exists(conn, "workspace_manifest"):
            update_workspace_manifest_repair(conn)
    return deleted


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

    before = _workspace_counts(conn, workspace_id=workspace_id)
    samples = _sample_rows(
        conn,
        workspace_id=workspace_id,
        limit_per_table=sample_limit,
    )
    protected_tables = [table for table in before if table == "workspace_manifest"]
    deleted: dict[str, int] = {}
    path_text: str | None = None
    if quarantine:
        assert quarantine_path is not None
        _write_quarantine(
            samples,
            workspace_id=workspace_id,
            include_default=include_default,
            quarantine_path=quarantine_path,
        )
        path_text = str(quarantine_path)
        deleted = _quarantine_rows(
            conn,
            workspace_id=workspace_id,
            include_default=include_default,
        )

    after = _workspace_counts(conn, workspace_id=workspace_id)
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
