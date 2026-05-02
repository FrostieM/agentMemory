"""Quarantine primitives for ``workspace_doctor.py``.

Split out of ``workspace_doctor_internals.py`` so each module stays
under the SLOC ceiling. Holds the FK-aware child-first ordering and
the JSON-export-then-DELETE pair.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_memory_lite.maintenance.workspace_doctor_internals import (
    WorkspacePollutionRow,
    quarantine_condition,
    quote_ident,
    table_exists,
    workspace_tables,
)
from agent_memory_lite.repositories.workspace_manifest_repo import (
    update_workspace_manifest_repair,
)
from agent_memory_lite.utils.time import iso_now


def child_first_tables(conn: sqlite3.Connection, tables: list[str]) -> list[str]:
    table_set = set(tables)
    parents: dict[str, set[str]] = {table: set() for table in tables}
    for table in tables:
        try:
            fk_rows = conn.execute(f"PRAGMA foreign_key_list({quote_ident(table)})").fetchall()
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


def write_quarantine(
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


def quarantine_rows(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    include_default: bool,
) -> dict[str, int]:
    condition = quarantine_condition(include_default=include_default)
    protected = {"workspace_manifest"}
    tables = [table for table in workspace_tables(conn) if table not in protected]
    deleted: dict[str, int] = {}
    with conn:
        for table in child_first_tables(conn, tables):
            cursor = conn.execute(
                f"DELETE FROM {quote_ident(table)} WHERE {condition}",
                (workspace_id,),
            )
            changed = max(int(cursor.rowcount if cursor.rowcount is not None else 0), 0)
            if changed:
                deleted[table] = changed
        if deleted and table_exists(conn, "workspace_manifest"):
            update_workspace_manifest_repair(conn)
    return deleted
